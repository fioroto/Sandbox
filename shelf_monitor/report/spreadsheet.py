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
from shelf_monitor.enrich import (
    EnrichmentStats,
    KEY_ANBIMA,
    KEY_CAPT_1M,
    KEY_CAPT_3M,
    KEY_CAPT_12M,
    KEY_CAPT_DATA,
    KEY_CAPT_JANELA,
    KEY_COTISTAS,
    KEY_COTISTAS_VAR_12M,
    KEY_COTISTAS_VAR_PCT_12M,
    KEY_CVM,
    KEY_GESTOR_OFICIAL,
    KEY_PL_INFORME,
    KEY_RENT_1M,
    KEY_RENT_3M,
    KEY_RENT_12M,
    KEY_RENT_JANELA,
    KEY_SITUACAO,
    KEY_TICKET,
    KEY_VOL_12M,
)
from shelf_monitor.models import (
    AssetClass,
    CvmSnapshot,
    FundOffer,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.profile import split_by_profile
from shelf_monitor.report.aggregate import ASSET_CLASS_LABELS, is_own_product

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
        f.on_listing
    )),
    ("Area logada", 13, lambda f: _yes_no(f.in_source("authenticated"))),
    ("Link", 42, lambda f: f.detail_url or ""),
    # No FIM de proposito: os indices _COL_* acima sao posicionais e a formula
    # de exclusividade deriva de len(_COLUMNS).
    ("Captacao liq. 3m (R$)", 20, lambda f: f.extra.get(KEY_CAPT_3M)),
    ("Captacao liq. 12m (R$)", 21, lambda f: f.extra.get(KEY_CAPT_12M)),
    ("Rentab. 12m", 13, lambda f: f.extra.get(KEY_RENT_12M)),
    ("Vol. 12m", 11, lambda f: f.extra.get(KEY_VOL_12M)),
    ("Ticket medio (R$)", 18, lambda f: f.extra.get(KEY_TICKET)),
    ("Cotistas", 12, lambda f: f.extra.get(KEY_COTISTAS)),
]


def _col_index(header: str) -> int:
    """Indice 1-based de uma coluna pelo cabecalho - imune a reordenacoes."""
    return next(i for i, (name, _, _) in enumerate(_COLUMNS, start=1) if name == header)

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
_COL_CAPT_3M = _col_index("Captacao liq. 3m (R$)")
_COL_CAPT_12M = _col_index("Captacao liq. 12m (R$)")
_COL_RENT_12M = _col_index("Rentab. 12m")
_COL_VOL_12M = _col_index("Vol. 12m")
_COL_TICKET = _col_index("Ticket medio (R$)")
_COL_COTISTAS = _col_index("Cotistas")
_COL_EXCLUSIVE = len(_COLUMNS) + 1
_PCT_FRACTION = "0.00%"


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
        headers.append("So nesta casa (por CNPJ)")

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
        # Restritos/exclusivos ficam fora das abas principais - moram na aba
        # auxiliar "Restritos", com o motivo de cada um.
        retail, _ = split_by_profile(snapshot.funds)
        for fund in sorted(retail, key=lambda f: f.name):
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
    for col in (_COL_CAPT_3M, _COL_CAPT_12M, _COL_TICKET):
        sheet.cell(row=row, column=col).number_format = _MONEY
    for col in (_COL_RENT_12M, _COL_VOL_12M):
        sheet.cell(row=row, column=col).number_format = _PCT_FRACTION
    sheet.cell(row=row, column=_COL_COTISTAS).number_format = _INT


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
    headers = ["Plataforma", *classes, "Total", "Com CNPJ", "Gestoras distintas", "So nesta casa"]
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
        '  "So nesta casa" conta fundos de varejo cujo CNPJ aparece em uma unica plataforma desta coleta;',
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


def _add_flows_sheet(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> None:
    """Aba "Captacao": fluxo do informe diario para os fundos da vitrine.

    Uma linha por fundo de varejo com informe casado, ordenada pela captacao
    liquida de 12m - captadores no topo, resgates no fim. A janela cobre so
    meses fechados; a coluna de janela diz quantos a serie realmente tem.
    """
    rows: list[tuple[str, FundOffer]] = []
    for snapshot in snapshots:
        retail, _ = split_by_profile(snapshot.funds)
        for fund in retail:
            if KEY_CAPT_12M in fund.extra or KEY_PL_INFORME in fund.extra:
                rows.append((_platform_label(snapshot.platform), fund))
    if not rows:
        return

    sheet = workbook.create_sheet("Captacao")
    headers = [
        "Plataforma", "Fundo", "CNPJ", "Gestora",
        "Captacao liq. 1m (R$)", "Captacao liq. 3m (R$)", "Captacao liq. 12m (R$)",
        "Janela (meses fechados)", "PL informe (R$)", "Cotistas", "Data-base",
        "Ticket medio (R$)", "Cotistas var. 12m", "Cotistas var. 12m (%)",
    ]
    widths = [20, 52, 20, 34, 20, 20, 21, 21, 20, 12, 12, 18, 17, 19]
    for index, (name, width) in enumerate(zip(headers, widths), start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    rows.sort(
        key=lambda item: item[1].extra.get(KEY_CAPT_12M) or 0.0, reverse=True
    )
    row = 2
    for label, fund in rows:
        values = [
            label, fund.name, format_cnpj(fund.cnpj) if fund.cnpj else "",
            fund.extra.get(KEY_GESTOR_OFICIAL) or fund.manager_name or "",
            fund.extra.get(KEY_CAPT_1M), fund.extra.get(KEY_CAPT_3M),
            fund.extra.get(KEY_CAPT_12M), fund.extra.get(KEY_CAPT_JANELA),
            fund.extra.get(KEY_PL_INFORME), fund.extra.get(KEY_COTISTAS),
            fund.extra.get(KEY_CAPT_DATA) or "",
            fund.extra.get(KEY_TICKET), fund.extra.get(KEY_COTISTAS_VAR_12M),
            fund.extra.get(KEY_COTISTAS_VAR_PCT_12M),
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        for col in (5, 6, 7, 9, 12):
            sheet.cell(row=row, column=col).number_format = _MONEY
        for col in (8, 10, 13):
            sheet.cell(row=row, column=col).number_format = _INT
        sheet.cell(row=row, column=14).number_format = _PCT_FRACTION
        row += 1

    sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row - 1}"
    sheet.freeze_panes = sheet.cell(row=2, column=3)

    note = row + 1
    for text in (
        "Fonte: informe diario da CVM (dados.cvm.gov.br), agregado por mes e por CNPJ",
        "de classe. As janelas somam apenas MESES FECHADOS - o mes corrente e parcial e",
        "fica fora; 'Janela' diz quantos meses a serie realmente cobre. Fundo anunciado",
        "pelo CNPJ do envelope soma a captacao das classes dele.",
    ):
        sheet.cell(row=note, column=1, value=text).font = _NOTE_FONT
        note += 1


def _add_performance_sheet(
    workbook: Workbook, snapshots: list[ShelfSnapshot], benchmarks: dict | None
) -> None:
    """Aba "Rentabilidade": retorno pela cota, risco e "% do CDI" como formula.

    O CDI fica em celulas fixas no topo (B1:B3) e a coluna "% do CDI 12m" e
    uma formula sobre elas: quem corrigir o CDI ou filtrar a aba ve o
    percentual acompanhar. Sem benchmark as celulas ficam vazias e a formula
    devolve vazio - nunca um numero inventado.
    """
    rows: list[tuple[str, FundOffer]] = []
    for snapshot in snapshots:
        retail, _ = split_by_profile(snapshot.funds)
        for fund in retail:
            if KEY_RENT_12M in fund.extra or KEY_RENT_1M in fund.extra:
                rows.append((_platform_label(snapshot.platform), fund))
    if not rows:
        return

    sheet = workbook.create_sheet("Rentabilidade")
    windows = (benchmarks or {}).get("windows") or {}
    for index, (label, key) in enumerate(
        (("CDI 12m (BCB)", "12m"), ("CDI 3m", "3m"), ("CDI 1m", "1m")), start=1
    ):
        sheet.cell(row=index, column=1, value=label).font = _NOTE_FONT
        cell = sheet.cell(row=index, column=2, value=windows.get(key))
        cell.font = _BODY_FONT
        cell.number_format = _PCT_FRACTION

    header_row = 5
    headers = [
        "Plataforma", "Fundo", "CNPJ", "Gestora", "Classe ANBIMA",
        "Rentab. 1m", "Rentab. 3m", "Rentab. 12m", "% do CDI 12m", "Vol. 12m",
        "Janela (meses de cota)", "Ticket medio (R$)", "Cotistas", "Data-base",
    ]
    widths = [20, 52, 20, 34, 30, 12, 12, 12, 14, 11, 20, 18, 12, 12]
    for index, (name, width) in enumerate(zip(headers, widths), start=1):
        sheet.cell(row=header_row, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, header_row, len(headers))

    rows.sort(key=lambda item: item[1].extra.get(KEY_RENT_12M) or float("-inf"), reverse=True)
    row = header_row + 1
    for label, fund in rows:
        values = [
            label, fund.name, format_cnpj(fund.cnpj) if fund.cnpj else "",
            fund.extra.get(KEY_GESTOR_OFICIAL) or fund.manager_name or "",
            fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM) or "",
            fund.extra.get(KEY_RENT_1M), fund.extra.get(KEY_RENT_3M),
            fund.extra.get(KEY_RENT_12M),
            f'=IF(OR(H{row}="",$B$1=""),"",H{row}/$B$1)',
            fund.extra.get(KEY_VOL_12M), fund.extra.get(KEY_RENT_JANELA),
            fund.extra.get(KEY_TICKET), fund.extra.get(KEY_COTISTAS),
            fund.extra.get(KEY_CAPT_DATA) or "",
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        for col in (6, 7, 8, 10):
            sheet.cell(row=row, column=col).number_format = _PCT_FRACTION
        sheet.cell(row=row, column=9).number_format = "0%"
        sheet.cell(row=row, column=12).number_format = _MONEY
        for col in (11, 13):
            sheet.cell(row=row, column=col).number_format = _INT
        row += 1

    sheet.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{row - 1}"
    sheet.freeze_panes = sheet.cell(row=header_row + 1, column=3)

    note = row + 1
    for text in (
        "Retorno pela cota do informe diario (dados.cvm.gov.br), sobre a MESMA serie de",
        "cota e apenas em meses fechados; sem a cota-base exatamente k meses antes, a",
        "janela fica vazia ('Janela' diz quantos meses de cota a serie tem). Volatilidade",
        "= desvio-padrao dos retornos diarios x raiz(252), com pelo menos 60 pares.",
        "'% do CDI' e formula sobre B1 (CDI 12m do BCB, serie 12) - vazio sem benchmark.",
    ):
        sheet.cell(row=note, column=1, value=text).font = _NOTE_FONT
        note += 1


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


_OWN_LABELS = {True: "Propria", False: "Terceiros", None: "-"}


def _add_listing_sheet(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> None:
    """Aba "Vitrine varejo": so o que o cliente ve no canal de cada casa.

    E a aba para tabela dinamica: classe ANBIMA oficial (quando a base de
    mercado foi importada), gestora, produto proprio vs terceiros, custo e
    ticket - tudo no mesmo grao, uma linha por fundo por vitrine.
    """
    from openpyxl.formatting.rule import ColorScaleRule

    sheet = workbook.create_sheet("Vitrine varejo")
    headers = [
        ("Plataforma", 20), ("Fundo", 52), ("CNPJ", 20),
        ("Classe oficial (ANBIMA ou CVM)", 34), ("Categoria na origem", 26),
        ("Gestora", 34), ("Produto", 12), ("Taxa adm (%)", 13),
        ("Aplicacao minima (R$)", 20), ("Liquidacao resgate (D+)", 21),
        ("Situacao CVM", 26), ("So nesta vitrine", 16), ("Fontes", 26),
    ]
    for index, (name, width) in enumerate(headers, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    row = 1
    for snapshot in snapshots:
        label = _platform_label(snapshot.platform)
        retail, _ = split_by_profile(snapshot.funds)
        for fund in retail:
            if not fund.on_listing:
                continue
            row += 1
            own = is_own_product(snapshot.platform, fund)
            values = [
                label,
                fund.name,
                format_cnpj(fund.cnpj) if fund.cnpj else "",
                fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM) or "",
                fund.raw_category or "",
                fund.extra.get(KEY_GESTOR_OFICIAL) or fund.manager_name or "",
                _OWN_LABELS[own],
                fund.admin_fee_pct,
                fund.min_initial,
                fund.redemption_payment_days,
                fund.extra.get(KEY_SITUACAO) or "",
                None,  # formula abaixo
                ", ".join(SOURCE_LABELS.get(s, s) for s in fund.sources),
            ]
            for index, value in enumerate(values, start=1):
                cell = sheet.cell(row=row, column=index, value=value)
                cell.font = _BODY_FONT
                cell.border = _BORDER
            sheet.cell(row=row, column=8).number_format = _PCT
            sheet.cell(row=row, column=9).number_format = _MONEY
            sheet.cell(row=row, column=10).number_format = _INT

    if row > 1:
        # Exclusivo entre vitrines: o CNPJ aparece uma vez so nesta aba.
        # Formula (e nao valor) para acompanhar filtros e correcoes manuais;
        # o intervalo e limitado as linhas de dado - COUNTIF de coluna inteira
        # ja provou travar o arquivo.
        cnpj_range = f"$C$2:$C${row}"
        for r in range(2, row + 1):
            cell = sheet.cell(
                row=r,
                column=12,
                value=(
                    f'=IF(C{r}="","",IF(COUNTIF({cnpj_range},C{r})=1,"Sim","Nao"))'
                ),
            )
            cell.font = _BODY_FONT
            cell.border = _BORDER
        # Leitura rapida de custo e ticket: escala de cor so no intervalo de dados.
        scale = ColorScaleRule(
            start_type="min", start_color="C6EFCE",
            end_type="max", end_color="FFC7CE",
        )
        sheet.conditional_formatting.add(f"H2:H{row}", scale)
        sheet.conditional_formatting.add(
            f"I2:I{row}",
            ColorScaleRule(
                start_type="min", start_color="C6EFCE",
                end_type="max", end_color="FFC7CE",
            ),
        )
    sheet.freeze_panes = "A2"


def _add_restricted_sheet(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> None:
    """Aba auxiliar: os fundos fora da analise de varejo, com o motivo.

    Eles nao somem do monitor - so nao contaminam comparacao de prateleira.
    """
    sheet = workbook.create_sheet("Restritos (auxiliar)")
    headers = [
        ("Plataforma", 20), ("Fundo", 52), ("CNPJ", 20), ("Motivo", 40),
        ("Classe oficial", 30), ("Gestora", 34), ("Na vitrine", 12), ("Fontes", 26),
    ]
    for index, (name, width) in enumerate(headers, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    row = 1
    for snapshot in snapshots:
        label = _platform_label(snapshot.platform)
        _, restricted = split_by_profile(snapshot.funds)
        for fund, reason in sorted(restricted, key=lambda pair: pair[0].name):
            row += 1
            values = [
                label, fund.name,
                format_cnpj(fund.cnpj) if fund.cnpj else "",
                reason,
                fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM) or "",
                fund.extra.get(KEY_GESTOR_OFICIAL) or fund.manager_name or "",
                "Sim" if fund.on_listing else "Nao",
                ", ".join(SOURCE_LABELS.get(s, s) for s in fund.sources),
            ]
            for index, value in enumerate(values, start=1):
                cell = sheet.cell(row=row, column=index, value=value)
                cell.font = _BODY_FONT
                cell.border = _BORDER
    if row == 1:
        sheet.cell(row=2, column=1, value="Nenhum fundo restrito/exclusivo identificado nesta coleta.").font = _NOTE_FONT
    sheet.freeze_panes = "A2"


def _add_movements_sheet(workbook: Workbook, diffs: list[ShelfDiff]) -> None:
    """Aba "Movimentos": o que entrou, saiu ou mudou desde a coleta anterior."""
    sheet = workbook.create_sheet("Movimentos")
    headers = [
        ("Plataforma", 20), ("Movimento", 14), ("Fundo", 52), ("CNPJ", 20),
        ("Na vitrine", 12), ("Detalhe", 60),
    ]
    for index, (name, width) in enumerate(headers, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    row = 1
    for diff in diffs:
        label = _platform_label(diff.platform)
        if diff.is_first_run:
            row += 1
            sheet.cell(row=row, column=1, value=label).font = _BODY_FONT
            sheet.cell(row=row, column=2, value="baseline").font = _NOTE_FONT
            sheet.cell(
                row=row, column=6,
                value="primeira coleta - sem base de comparacao",
            ).font = _NOTE_FONT
            continue
        if diff.suspect:
            row += 1
            sheet.cell(row=row, column=1, value=label).font = _BODY_FONT
            sheet.cell(row=row, column=2, value="SUSPEITO").font = _ALERT_FONT
            sheet.cell(
                row=row, column=6,
                value=(
                    "coleta parcial ou queda brusca - saidas nao listadas para "
                    "nao virar churn falso"
                ),
            ).font = _ALERT_FONT

        entries: list[tuple[str, str, str, str, str]] = []
        from shelf_monitor.profile import restriction_reason

        def is_retail(fund) -> bool:
            return restriction_reason(fund) is None

        for fund in filter(is_retail, diff.added):
            entries.append((
                "entrada", fund.name,
                format_cnpj(fund.cnpj) if fund.cnpj else "",
                "Sim" if fund.on_listing else "Nao", "",
            ))
        if not diff.suspect:
            for fund in filter(is_retail, diff.removed):
                entries.append((
                    "saida", fund.name,
                    format_cnpj(fund.cnpj) if fund.cnpj else "",
                    "Sim" if fund.on_listing else "Nao", "",
                ))
        for change in diff.changed:
            detail = "; ".join(
                f"{c.field}: {c.old} -> {c.new}" for c in change.changes
            )
            cnpj = change.identity if change.identity.isdigit() else ""
            entries.append((
                "alteracao", change.name,
                format_cnpj(cnpj) if cnpj else "", "", detail,
            ))

        for movement, name, cnpj, listing, detail in entries:
            row += 1
            for index, value in enumerate(
                (label, movement, name, cnpj, listing, detail), start=1
            ):
                cell = sheet.cell(row=row, column=index, value=value)
                cell.font = _BODY_FONT
                cell.border = _BORDER
    sheet.freeze_panes = "A2"


def _add_methodology_sheet(
    workbook: Workbook,
    snapshots: list[ShelfSnapshot],
    enrichment: EnrichmentStats | None,
    generated_at: datetime,
) -> None:
    """Aba "Metodologia": proveniencia e cobertura - o que um comite pergunta."""
    sheet = workbook.create_sheet("Metodologia")
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 90

    def line(row: int, key: str, value: str, font=None) -> int:
        sheet.cell(row=row, column=1, value=key).font = _HEADER_FONT if font is None else font
        sheet.cell(row=row, column=1).fill = _HEADER_FILL if font is None else PatternFill()
        sheet.cell(row=row, column=2, value=value).font = _BODY_FONT
        return row + 1

    row = 1
    sheet.cell(row=row, column=1, value="Metodologia e proveniencia").font = _TITLE_FONT
    row += 2

    row = line(row, "Gerado em", generated_at.strftime("%d/%m/%Y %H:%M"))
    row = line(
        row, "Vitrine (definicao)",
        "Fundo presente no canal da propria casa: site publico, API do portal ou "
        "area logada. Open Finance NAO conta como vitrine - e o catalogo "
        "regulatorio (tudo que a casa administra), usado aqui para enriquecer "
        "campos e medir o catalogo.",
    )
    row = line(
        row, "Perfil de varejo",
        "Fundos exclusivos/restritos (registro CVM, publico profissional, flag "
        "da casa ou nome) ficam FORA das abas de analise e moram na aba "
        "'Restritos (auxiliar)' com o motivo. Qualificado nao e motivo de corte.",
    )
    row = line(
        row, "Precedencia de fontes",
        "Open Finance > area logada > API do portal > navegador > HTML. Quando "
        "duas fontes discordam de um campo, vence a de maior confianca; as "
        "demais preenchem lacunas.",
    )
    if enrichment is not None:
        identifiable = max(enrichment.with_cnpj, enrichment.with_cvm_code)
        pct = (
            f"{100 * enrichment.matched / identifiable:.0f}%" if identifiable else "n/a"
        )
        row = line(
            row, "Normalizacao ANBIMA",
            f"Join com a base oficial CVM pelo CNPJ da classe, pelo CNPJ do fundo "
            f"e pelo codigo CVM da subclasse ({enrichment.matched} de "
            f"{identifiable} fundos identificaveis casaram - {pct}; "
            f"{enrichment.matched_by_cvm_code} deles so pelo codigo). Classe "
            "ANBIMA, gestor e situacao vem do registro oficial; a classificacao "
            "publicada pela casa, quando diverge, fica na coluna "
            "classificacao_anbima_casa.",
        )
        row = line(
            row, "Gestora",
            f"Vem do registro da CVM sempre que o fundo casa com a base "
            f"({enrichment.managers_from_cvm} fundos nesta geracao) - e o que "
            "faz a mesma gestora ter um nome so nas cinco casas. O nome "
            "publicado pela casa, quando diverge, fica em gestor_casa. Sem "
            f"match, {enrichment.managers_inferred} gestoras foram inferidas "
            "pelo prefixo do nome do fundo e vao marcadas como inferidas.",
        )
    else:
        row = line(
            row, "Normalizacao ANBIMA",
            "NAO aplicada nesta geracao: base de mercado ausente. Rode "
            "`python -m shelf_monitor market` para importar a base oficial da "
            "CVM e reexporte.",
            font=_ALERT_FONT,
        )

    with_flows = sum(
        1
        for snapshot in snapshots
        for fund in snapshot.funds
        if KEY_CAPT_12M in fund.extra
    )
    if with_flows:
        row = line(
            row, "Captacao (CVM)",
            f"Informe diario agregado por mes e CNPJ de classe ({with_flows} "
            "fundos com informe casado nesta geracao). As janelas 1m/3m/12m "
            "somam apenas meses FECHADOS - o mes corrente e parcial e fica "
            "fora das somas. Ver a aba 'Captacao'.",
        )
        with_returns = sum(
            1
            for snapshot in snapshots
            for fund in snapshot.funds
            if KEY_RENT_12M in fund.extra or KEY_RENT_1M in fund.extra
        )
        row = line(
            row, "Rentabilidade (CVM)",
            f"Retorno pela cota do informe, sobre a mesma serie de cota e em meses "
            f"fechados ({with_returns} fundos com cota casada). Estrito: sem a "
            "cota-base exatamente k meses antes a janela fica vazia. Volatilidade "
            "anualizada dos retornos diarios (minimo 60 pares). '% do CDI' compara "
            "com a serie 12 do BCB na mesma janela; sem ela, nao aparece. Ver a "
            "aba 'Rentabilidade'.",
        )
        row = line(
            row, "Cotistas e ticket (CVM)",
            "Cotistas e PL por cotista (ticket medio) do informe mais fresco; "
            "variacao de cotistas sobre meses fechados. Fundo anunciado pelo "
            "envelope soma os cotistas das classes e nao recebe retorno (cotas "
            "diferentes nao se combinam). Colunas na aba 'Captacao'.",
        )
    else:
        row = line(
            row, "Captacao (CVM)",
            "Sem captacao nesta geracao: importe o informe diario com "
            "`python -m shelf_monitor market` e reexporte.",
        )

    row += 1
    sheet.cell(row=row, column=1, value="Coleta por casa").font = _TITLE_FONT
    row += 1
    for snapshot in snapshots:
        parts = [f"status={snapshot.status.value}"]
        if snapshot.source_counts:
            parts.append(
                "fontes: "
                + ", ".join(f"{k}={v}" for k, v in snapshot.source_counts.items())
            )
        listing = sum(1 for f in snapshot.funds if f.on_listing)
        parts.append(f"vitrine={listing} de {len(snapshot.funds)}")
        if snapshot.error:
            parts.append(f"obs: {snapshot.error[:180]}")
        row = line(
            row,
            _platform_label(snapshot.platform),
            f"{snapshot.collected_at.strftime('%d/%m/%Y %H:%M')} - "
            + " | ".join(parts),
        )


def render_spreadsheet(
    config: Config,
    snapshots: list[ShelfSnapshot],
    cvm: CvmSnapshot | None = None,
    generated_at: datetime | None = None,
    path: Path | None = None,
    diffs: list[ShelfDiff] | None = None,
    enrichment: EnrichmentStats | None = None,
    benchmarks: dict | None = None,
) -> Path:
    """Gera a planilha das prateleiras. Devolve o caminho do arquivo."""
    generated_at = generated_at or datetime.now()
    usable = [s for s in snapshots if s.funds]

    workbook = Workbook()
    last_row = _add_consolidated(workbook, usable)
    _add_listing_sheet(workbook, usable)
    _add_restricted_sheet(workbook, usable)
    if diffs:
        _add_movements_sheet(workbook, diffs)
    _add_platform_sheets(workbook, usable)
    _add_flows_sheet(workbook, usable)
    _add_performance_sheet(workbook, usable, benchmarks)
    if cvm and cvm.funds:
        _add_cvm(workbook, cvm)
    _add_source_coverage(workbook, usable)
    _add_collection_health(workbook, snapshots, cvm)
    _add_methodology_sheet(workbook, snapshots, enrichment, generated_at)
    _add_summary(workbook, usable, snapshots, last_row, generated_at)

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = path or (
        config.reports_dir / f"prateleiras-{generated_at.date().isoformat()}.xlsx"
    )
    workbook.save(path)
    return path
