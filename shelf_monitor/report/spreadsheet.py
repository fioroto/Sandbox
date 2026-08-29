"""Exportacao das prateleiras coletadas para planilha.

A planilha e o formato em que o time de produto de fato trabalha os dados:
filtra, ordena, cruza com outras fontes. Por isso as agregacoes saem como
formulas e nao como numeros ja calculados - se voce filtrar a aba consolidada
ou corrigir uma linha, o resumo acompanha.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from shelf_monitor.config import PLATFORMS, Config
from shelf_monitor.models import (
    AssetClass,
    CvmSnapshot,
    FundOffer,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.report.aggregate import ASSET_CLASS_LABELS

FONT = "Arial"

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFF")
_TITLE_FONT = Font(name=FONT, size=13, bold=True)
_BODY_FONT = Font(name=FONT, size=10)
_NOTE_FONT = Font(name=FONT, size=9, italic=True, color="595959")
_ALERT_FONT = Font(name=FONT, size=10, bold=True, color="C00000")
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(bottom=_THIN)

_MONEY = '#,##0;(#,##0);-'
_PCT = '0.00"%";-;-'
_INT = '0;-;-'

# (cabecalho, largura, extrator) - a ordem aqui e a ordem das colunas.
_COLUMNS: list[tuple[str, int, object]] = [
    ("Plataforma", 20, None),  # preenchido pelo chamador
    ("Fundo", 52, lambda f: f.name),
    ("CNPJ", 20, lambda f: format_cnpj(f.cnpj) if f.cnpj else ""),
    ("Gestora", 34, lambda f: f.manager_name or ""),
    ("CNPJ da gestora", 20, lambda f: format_cnpj(f.manager_cnpj) if f.manager_cnpj else ""),
    ("Administrador", 30, lambda f: f.admin_name or ""),
    ("Classe", 16, lambda f: ASSET_CLASS_LABELS.get(f.asset_class, "Outros")),
    ("Categoria na origem", 30, lambda f: f.raw_category or ""),
    ("Aplicacao minima (R$)", 20, lambda f: f.min_initial),
    ("Taxa adm (%)", 14, lambda f: f.admin_fee_pct),
    ("Taxa perf (%)", 14, lambda f: f.performance_fee.amount_pct if f.performance_fee else None),
    ("Benchmark", 14, lambda f: (f.performance_fee.benchmark if f.performance_fee else "") or ""),
    ("Cotizacao resgate (D+)", 20, lambda f: f.redemption_quotation_days),
    ("Liquidacao resgate (D+)", 21, lambda f: f.redemption_payment_days),
    ("Carencia (dias)", 15, lambda f: f.grace_days),
    ("Tributacao", 16, lambda f: f.taxation or ""),
    ("Fechado p/ captacao", 19, lambda f: _yes_no(f.closed_for_investment)),
    ("Fontes", 26, lambda f: ", ".join(SOURCE_LABELS.get(s, s) for s in f.sources)),
    ("Nº de fontes", 12, lambda f: len(f.sources) or None),
    # Uma coluna por fonte: e o que permite filtrar "so no site" ou montar uma
    # tabela dinamica cruzando plataforma x fonte.
    ("Open Finance", 13, lambda f: _yes_no(f.in_source("open-finance"))),
    ("Prateleira (site)", 17, lambda f: _yes_no(
        f.in_source("html-scrape") or f.in_source("playwright") or f.in_source("api-json")
    )),
    ("Area logada", 13, lambda f: _yes_no(f.in_source("authenticated"))),
    ("Link", 42, lambda f: f.detail_url or ""),
]

SOURCE_LABELS = {
    "open-finance": "Open Finance",
    "html-scrape": "Site (HTML)",
    "playwright": "Site (navegador)",
    "api-json": "API interna",
    "authenticated": "Area logada",
}

# Colunas por indice (1-based), para as formulas e formatos.
_COL_PLATFORM = 1
_COL_NAME = 2
_COL_CNPJ = 3
_COL_CLASS = 7
_COL_MIN = 9
_COL_ADMIN_FEE = 10
_COL_PERF_FEE = 11
_COL_QUOTATION = 13
_COL_PAYMENT = 14
_COL_GRACE = 15
_COL_EXCLUSIVE = len(_COLUMNS) + 1


def _yes_no(value: bool | None) -> str:
    if value is None:
        return ""
    return "Sim" if value else "Nao"


def _platform_label(platform_id: str) -> str:
    cfg = PLATFORMS.get(platform_id)
    return cfg.label if cfg else platform_id


def _style_header(sheet: Worksheet, row: int, columns: int) -> None:
    for col in range(1, columns + 1):
        cell = sheet.cell(row=row, column=col)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[row].height = 30


def _write_fund_rows(
    sheet: Worksheet,
    snapshots: list[ShelfSnapshot],
    *,
    header_row: int,
    with_exclusive: bool,
) -> int:
    """Escreve o cabecalho e uma linha por fundo. Devolve a ultima linha usada."""
    headers = [name for name, _, _ in _COLUMNS]
    if with_exclusive:
        headers.append("Exclusivo (por CNPJ)")

    for index, name in enumerate(headers, start=1):
        sheet.cell(row=header_row, column=index, value=name)
    _style_header(sheet, header_row, len(headers))

    for index, (_, width, _) in enumerate(_COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    if with_exclusive:
        sheet.column_dimensions[get_column_letter(_COL_EXCLUSIVE)].width = 19

    row = header_row + 1
    for snapshot in snapshots:
        label = _platform_label(snapshot.platform)
        for fund in sorted(snapshot.funds, key=lambda f: f.name):
            sheet.cell(row=row, column=_COL_PLATFORM, value=label)
            for index, (_, _, extract) in enumerate(_COLUMNS, start=1):
                if extract is None:
                    continue
                sheet.cell(row=row, column=index, value=extract(fund))
            _style_row(sheet, row, len(headers))
            row += 1

    last = row - 1
    if with_exclusive:
        # Escrito depois de conhecer a ultima linha: o intervalo precisa ser
        # exato. Com a coluna inteira ($C:$C), cada fundo varreria um milhao de
        # celulas e a planilha levaria minutos para recalcular.
        for target in range(header_row + 1, last + 1):
            _write_exclusive_formula(sheet, target, header_row + 1, last)
    if last >= header_row + 1:
        sheet.auto_filter.ref = (
            f"A{header_row}:{get_column_letter(len(headers))}{last}"
        )
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=3)
    return last


def _write_exclusive_formula(
    sheet: Worksheet, row: int, first_row: int, last_row: int
) -> None:
    """Exclusividade como formula: reage a filtros e correcoes na propria aba.

    Um fundo sem CNPJ nao entra na conta - comparar por nome entre casas
    produziria exclusividade falsa.
    """
    col = get_column_letter(_COL_CNPJ)
    cnpj = f"${col}{row}"
    data_range = f"${col}${first_row}:${col}${last_row}"
    sheet.cell(
        row=row,
        column=_COL_EXCLUSIVE,
        value=f'=IF({cnpj}="","",IF(COUNTIF({data_range},{cnpj})=1,"Sim","Nao"))',
    )
    sheet.cell(row=row, column=_COL_EXCLUSIVE).font = _BODY_FONT
    sheet.cell(row=row, column=_COL_EXCLUSIVE).border = _BORDER


def _style_row(sheet: Worksheet, row: int, columns: int) -> None:
    for col in range(1, columns + 1):
        cell = sheet.cell(row=row, column=col)
        cell.font = _BODY_FONT
        cell.border = _BORDER
    sheet.cell(row=row, column=_COL_CNPJ).alignment = Alignment(horizontal="left")
    sheet.cell(row=row, column=_COL_MIN).number_format = _MONEY
    sheet.cell(row=row, column=_COL_ADMIN_FEE).number_format = _PCT
    sheet.cell(row=row, column=_COL_PERF_FEE).number_format = _PCT
    for col in (_COL_QUOTATION, _COL_PAYMENT, _COL_GRACE):
        sheet.cell(row=row, column=col).number_format = _INT


def _add_consolidated(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> int:
    sheet = workbook.active
    sheet.title = "Consolidado"
    last = _write_fund_rows(sheet, snapshots, header_row=1, with_exclusive=True)
    return last


def _add_platform_sheets(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> None:
    for snapshot in snapshots:
        # O nome da aba tem limite de 31 caracteres no formato xlsx.
        title = _platform_label(snapshot.platform)[:31]
        sheet = workbook.create_sheet(title)
        _write_fund_rows(sheet, [snapshot], header_row=1, with_exclusive=False)


def _add_summary(
    workbook: Workbook,
    snapshots: list[ShelfSnapshot],
    all_snapshots: list[ShelfSnapshot],
    last_row: int,
    generated_at: datetime,
) -> None:
    """Resumo por plataforma x classe, todo em formulas sobre o Consolidado."""
    sheet = workbook.create_sheet("Resumo", 0)
    sheet.column_dimensions["A"].width = 24
    sheet.cell(row=1, column=1, value="Prateleiras de fundos - resumo").font = _TITLE_FONT
    sheet.cell(
        row=2, column=1,
        value=f"Coleta de {generated_at.strftime('%d/%m/%Y %H:%M')}",
    ).font = _NOTE_FONT

    classes = [ASSET_CLASS_LABELS[c] for c in AssetClass]
    headers = ["Plataforma", *classes, "Total", "Com CNPJ", "Gestoras distintas", "Exclusivos"]
    header_row = 4
    for index, name in enumerate(headers, start=1):
        sheet.cell(row=header_row, column=index, value=name)
        if index > 1:
            sheet.column_dimensions[get_column_letter(index)].width = 15
    _style_header(sheet, header_row, len(headers))

    plat_col = get_column_letter(_COL_PLATFORM)
    class_col = get_column_letter(_COL_CLASS)
    cnpj_col = get_column_letter(_COL_CNPJ)
    excl_col = get_column_letter(_COL_EXCLUSIVE)
    data = f"Consolidado!${{col}}$2:${{col}}${last_row}"

    row = header_row + 1
    for snapshot in snapshots:
        label = _platform_label(snapshot.platform)
        sheet.cell(row=row, column=1, value=label).font = _BODY_FONT
        plat_range = data.format(col=plat_col)
        plat_ref = f'$A{row}'

        for offset, class_label in enumerate(classes, start=2):
            cell = sheet.cell(
                row=row, column=offset,
                value=(
                    f'=COUNTIFS({plat_range},{plat_ref},'
                    f'{data.format(col=class_col)},{get_column_letter(offset)}${header_row})'
                ),
            )
            cell.number_format = _INT

        total_col = len(classes) + 2
        sheet.cell(
            row=row, column=total_col,
            value=f'=COUNTIF({plat_range},{plat_ref})',
        ).number_format = _INT
        sheet.cell(
            row=row, column=total_col + 1,
            value=(
                f'=COUNTIFS({plat_range},{plat_ref},'
                f'{data.format(col=cnpj_col)},"<>")'
            ),
        ).number_format = _INT
        # Gestoras distintas nao tem COUNTIF equivalente sem funcao de matriz;
        # o valor vem da coleta e esta documentado como tal na legenda.
        managers = len({f.manager_name for f in snapshot.funds if f.manager_name})
        sheet.cell(row=row, column=total_col + 2, value=managers).number_format = _INT
        sheet.cell(
            row=row, column=total_col + 3,
            value=(
                f'=COUNTIFS({plat_range},{plat_ref},'
                f'{data.format(col=excl_col)},"Sim")'
            ),
        ).number_format = _INT

        for col in range(1, len(headers) + 1):
            cell = sheet.cell(row=row, column=col)
            cell.font = _BODY_FONT if col == 1 else Font(name=FONT, size=10)
            cell.border = _BORDER
        row += 1

    note_row = row + 1
    unavailable = [s for s in all_snapshots if s.status == SnapshotStatus.UNAVAILABLE]
    if unavailable:
        sheet.cell(
            row=note_row, column=1,
            value="Fontes indisponiveis nesta coleta (nao aparecem acima):",
        ).font = _ALERT_FONT
        note_row += 1
        for snapshot in unavailable:
            sheet.cell(
                row=note_row, column=1,
                value=f"  {_platform_label(snapshot.platform)}: {snapshot.error or 'sem detalhe'}",
            ).font = _NOTE_FONT
            note_row += 1
        note_row += 1

    for line in (
        "Como ler esta planilha:",
        "  Todos os numeros acima sao formulas sobre a aba Consolidado - filtrar ou corrigir la atualiza aqui.",
        '  "Exclusivos" conta fundos cujo CNPJ aparece em uma unica plataforma desta coleta;',
        "  fundos sem CNPJ ficam fora dessa conta, porque comparar por nome entre casas gera falso exclusivo.",
        "  \"Gestoras distintas\" e um valor da coleta, nao uma formula: contar valores unicos exigiria",
        "  funcao de matriz, que nem todo leitor de planilha avalia da mesma forma.",
        "  Uma plataforma com poucos fundos pode ser fonte parcial, nao prateleira enxuta - confira a aba Coleta.",
    ):
        sheet.cell(row=note_row, column=1, value=line).font = _NOTE_FONT
        note_row += 1


def _add_cvm(workbook: Workbook, cvm: CvmSnapshot) -> None:
    sheet = workbook.create_sheet("Radar CVM")
    headers = [
        "Casa", "Fundo", "CNPJ", "Gestora", "Administrador", "Tipo",
        "Situacao", "Pre-operacional", "Data de registro", "Inicio da situacao",
        "Patrimonio liquido (R$)",
    ]
    widths = [20, 52, 20, 34, 30, 12, 26, 16, 17, 18, 22]
    for index, (name, width) in enumerate(zip(headers, widths), start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    row = 2
    # Pre-operacionais primeiro: sao o sinal antecipado que justifica a aba.
    ordered = sorted(
        cvm.funds,
        key=lambda f: (not f.is_pre_operational, f.registered_at or "", f.name),
    )
    for fund in ordered:
        values = [
            _platform_label(fund.house), fund.name, format_cnpj(fund.cnpj),
            fund.manager_name or "", fund.admin_name or "", fund.fund_type or "",
            fund.situation or "", "Sim" if fund.is_pre_operational else "Nao",
            fund.registered_at or "", fund.situation_started_at or "", fund.pl,
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        sheet.cell(row=row, column=len(headers)).number_format = _MONEY
        if fund.is_pre_operational:
            for index in range(1, len(headers) + 1):
                sheet.cell(row=row, column=index).font = Font(
                    name=FONT, size=10, bold=True, color="1F3864"
                )
        row += 1

    if row > 2:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row - 1}"
    sheet.freeze_panes = sheet.cell(row=2, column=3)


def _add_source_coverage(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> None:
    """Quanto cada fonte contribuiu por casa, e o que so ela enxergou.

    E a aba que responde se vale manter o scraping de uma casa: se o site nao
    traz nada que o Open Finance ja nao tenha, ele so custa tempo de coleta.
    """
    sheet = workbook.create_sheet("Fontes")
    headers = [
        "Plataforma", "Fonte", "Fundos trazidos", "So nesta fonte",
        "Total da casa (uniao)", "% do total",
    ]
    widths = [22, 20, 16, 16, 21, 12]
    for index, (name, width) in enumerate(zip(headers, widths), start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    row = 2
    for snapshot in snapshots:
        if not snapshot.funds:
            continue
        label = _platform_label(snapshot.platform)
        total = len(snapshot.funds)

        # Exclusivos por fonte sao contados aqui e nao por formula: dependem de
        # ler a lista de fontes de cada fundo, que a planilha guarda como texto.
        exclusive: dict[str, int] = {}
        for fund in snapshot.funds:
            if len(fund.sources) == 1:
                exclusive[fund.sources[0]] = exclusive.get(fund.sources[0], 0) + 1

        sources = snapshot.source_counts or {
            s: total for s in (snapshot.sources_used or ["desconhecida"])
        }
        for source, count in sorted(sources.items(), key=lambda kv: -kv[1]):
            values = [
                label, SOURCE_LABELS.get(source, source), count,
                exclusive.get(source, 0), total,
            ]
            for index, value in enumerate(values, start=1):
                cell = sheet.cell(row=row, column=index, value=value)
                cell.font = _BODY_FONT
                cell.border = _BORDER
            share = sheet.cell(row=row, column=6, value=f"=IF($E{row}=0,0,$C{row}/$E{row})")
            share.number_format = "0.0%"
            share.font = _BODY_FONT
            share.border = _BORDER
            for col in (3, 4, 5):
                sheet.cell(row=row, column=col).number_format = _INT
            row += 1

    note = row + 1
    for line in (
        '"Fundos trazidos" e quanto aquela fonte devolveu sozinha; a soma pode passar do total',
        "da casa, porque o mesmo fundo costuma aparecer em mais de uma fonte.",
        '"So nesta fonte" e o que se perderia desligando aquela coleta - o numero que decide',
        "se manter o scraping de uma casa compensa o tempo que ele custa.",
    ):
        sheet.cell(row=note, column=1, value=line).font = _NOTE_FONT
        note += 1


def _add_collection_health(
    workbook: Workbook, snapshots: list[ShelfSnapshot], cvm: CvmSnapshot | None
) -> None:
    sheet = workbook.create_sheet("Coleta")
    headers = ["Fonte", "Status", "Fontes consultadas", "Fundos", "Observacao"]
    widths = [24, 14, 34, 10, 90]
    for index, (name, width) in enumerate(zip(headers, widths), start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    row = 2
    for snapshot in snapshots:
        values = [
            _platform_label(snapshot.platform), snapshot.status.value,
            ", ".join(SOURCE_LABELS.get(s, s) for s in snapshot.sources_used) or "-",
            len(snapshot.funds), snapshot.error or "",
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        if snapshot.status != SnapshotStatus.OK:
            sheet.cell(row=row, column=2).font = _ALERT_FONT
        row += 1

    if cvm:
        values = [
            "Cadastro CVM", cvm.status.value, "download", len(cvm.funds),
            cvm.error or "",
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        if cvm.status != SnapshotStatus.OK:
            sheet.cell(row=row, column=2).font = _ALERT_FONT


def render_spreadsheet(
    config: Config,
    snapshots: list[ShelfSnapshot],
    cvm: CvmSnapshot | None = None,
    generated_at: datetime | None = None,
    path: Path | None = None,
) -> Path:
    """Gera a planilha das prateleiras. Devolve o caminho do arquivo."""
    generated_at = generated_at or datetime.now()
    usable = [s for s in snapshots if s.funds]

    workbook = Workbook()
    last_row = _add_consolidated(workbook, usable)
    _add_platform_sheets(workbook, usable)
    if cvm and cvm.funds:
        _add_cvm(workbook, cvm)
    _add_source_coverage(workbook, usable)
    _add_collection_health(workbook, snapshots, cvm)
    _add_summary(workbook, usable, snapshots, last_row, generated_at)

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = path or (
        config.reports_dir / f"prateleiras-{generated_at.date().isoformat()}.xlsx"
    )
    workbook.save(path)
    return path
