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

from shelf_monitor.config import Config
from shelf_monitor.enrich import (
    EnrichmentStats,
    KEY_ANBIMA,
    KEY_CVM,
    KEY_GESTOR_OFICIAL,
    KEY_SITUACAO,
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
from shelf_monitor.report.shared import rotulo_da_casa
from shelf_monitor.report.aggregate import (
    ASSET_CLASS_LABELS,
    build_gap_block,
    is_own_product,
)

FONT = "Arial"

_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
# Entrada e saida de dinheiro nas abas de captacao - os mesmos tons do
# relatorio de ETF, para o verde significar a mesma coisa nos dois arquivos.
_GREEN_FILL = PatternFill("solid", fgColor="E2EFDA")
_RED_FILL = PatternFill("solid", fgColor="FCE4D6")
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


_platform_label = rotulo_da_casa


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


def _add_consolidated(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> int:
    sheet = workbook.active
    sheet.title = "Consolidado"
    # `with_exclusive=False`: a exclusividade NAO se mede aqui. Esta aba e o
    # catalogo (todo fundo de varejo, inclusive o que so o Open Finance
    # publica), e a coluna contava o CNPJ nesse universo - devolvendo resposta
    # diferente da coluna quase homonima da aba "Vitrine varejo", na mesma
    # pasta. Exclusividade e conceito de vitrine e mora la, uma vez so.
    last = _write_fund_rows(sheet, snapshots, header_row=1, with_exclusive=False)
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
    listing_last_row: int = 1,
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
    headers = ["Plataforma", *classes, "Total", "Com CNPJ", "Gestoras distintas",
               "So nesta vitrine"]
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
        # "So nesta vitrine" conta na aba Vitrine varejo, nao no Consolidado:
        # exclusividade e conceito de vitrine. Contada sobre o Consolidado, ela
        # incluia o catalogo Open Finance e inflava o numero - no BTG isso dava
        # 5.767 "exclusivos" contra 284 da XP, todos fundos que ele administra e
        # nenhum cliente ve.
        if listing_last_row > 1:
            sheet.cell(
                row=row, column=total_col + 3,
                value=(
                    f"=COUNTIFS('Vitrine varejo'!$A$2:$A${listing_last_row},{plat_ref},"
                    f"'Vitrine varejo'!$L$2:$L${listing_last_row},\"Sim\")"
                ),
            ).number_format = _INT
        else:
            sheet.cell(row=row, column=total_col + 3, value=0).number_format = _INT

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
        "  As contagens por classe e os totais sao formulas sobre a aba Consolidado (o catalogo);",
        '  "So nesta vitrine" e formula sobre a aba Vitrine varejo, e nao sobre o Consolidado: exclusividade',
        "  se mede na vitrine. Contada sobre o catalogo ela somava fundo que a casa administra e ninguem ve.",
        "  Fundos sem CNPJ ficam fora dessa conta, porque comparar por nome entre casas gera falso exclusivo.",
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


_OWN_LABELS = {True: "Propria", False: "Terceiros", None: "-"}


def _add_listing_sheet(workbook: Workbook, snapshots: list[ShelfSnapshot]) -> int:
    """Aba "Vitrine varejo": so o que o cliente ve no canal de cada casa.

    E a aba para tabela dinamica: classe ANBIMA oficial (quando a base de
    mercado foi importada), gestora, produto proprio vs terceiros, custo e
    ticket - tudo no mesmo grao, uma linha por fundo por vitrine.
    Devolve a ultima linha com dado - a aba de gaps aponta formulas para ca.
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
    return row


def _add_gaps_sheets(
    workbook: Workbook, snapshots: list[ShelfSnapshot], listing_last_row: int
) -> None:
    """Abas "Gaps XP" e "Gaps por categoria": vitrine concorrente x vitrine XP.

    Os numeros vem de build_gap_block - os mesmos do dashboard, digest e deck.
    A coluna de verificacao e formula sobre a aba "Vitrine varejo" (intervalo
    limitado as linhas de dado): reage a correcoes manuais na vitrine sem
    reexportar.
    """
    block = build_gap_block(snapshots)

    sheet = workbook.create_sheet("Gaps XP")
    if not block.get("available"):
        sheet.column_dimensions["A"].width = 110
        sheet.cell(row=1, column=1, value="Gaps de prateleira da XP").font = _TITLE_FONT
        sheet.cell(
            row=3, column=1,
            value=f"Analise suprimida nesta execucao: {block.get('reason')}.",
        ).font = _ALERT_FONT
        sheet.cell(
            row=4, column=1,
            value="Sem a vitrine da XP coletada, todo fundo concorrente pareceria "
            "gap - o bloco prefere se calar a inventar numero.",
        ).font = _NOTE_FONT
        return

    headers = [
        ("Fundo", 52), ("CNPJ", 20), ("Categoria oficial", 34), ("Gestora", 34),
        ("Nº casas", 10), ("Casas", 30), ("Menor minimo (R$)", 18),
        ("Menor taxa adm (%)", 17), ("PL CVM (R$)", 18),
        ("Gestora na vitrine XP", 20), ("Possivel equivalente XP (heuristica)", 40),
        ("Verificacao vs. Vitrine varejo", 26),
    ]
    for index, (name, width) in enumerate(headers, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(headers))

    baseline_label = block["baseline_label"].replace('"', "")
    row = 1
    for fund in block["funds"]:
        row += 1
        values = [
            fund["name"],
            fund["cnpj_display"],
            fund["category"],
            fund["manager"] or "",
            fund["houses_count"],
            fund["houses_label"],
            fund["min_initial"],
            fund["admin_fee_pct"],
            fund["pl_cvm"],
            _yes_no(fund["manager_on_xp"]),
            fund["possible_xp_equivalent"] or "",
            None,  # formula abaixo
        ]
        for index, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        sheet.cell(row=row, column=5).number_format = _INT
        sheet.cell(row=row, column=7).number_format = _MONEY
        sheet.cell(row=row, column=8).number_format = _PCT
        sheet.cell(row=row, column=9).number_format = _MONEY
        if listing_last_row > 1:
            plat_range = f"'Vitrine varejo'!$A$2:$A${listing_last_row}"
            cnpj_range = f"'Vitrine varejo'!$C$2:$C${listing_last_row}"
            sheet.cell(
                row=row, column=12,
                value=(
                    f'=IF($B{row}="","",IF(COUNTIFS({plat_range},'
                    f'"{baseline_label}",{cnpj_range},$B{row})=0,'
                    f'"Confirma gap","{baseline_label} vitrina - revisar"))'
                ),
            )
            sheet.cell(row=row, column=12).font = _BODY_FONT
            sheet.cell(row=row, column=12).border = _BORDER

    if row > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{row}"
    else:
        sheet.cell(
            row=2, column=1,
            value="Nenhum gap identificado: toda vitrine concorrente coletada "
            "esta contida na vitrine da XP.",
        ).font = _NOTE_FONT
    note_row = row + 2
    sheet.cell(
        row=note_row, column=1,
        value="Gap = fundo de varejo na vitrine de 1+ concorrente e fora da "
        "vitrine da XP (por CNPJ). Comparacao vitrine x vitrine: o catalogo "
        "Open Finance nao conta em nenhum dos lados. As colunas de gestora e "
        "equivalente sao heuristicas - informam, nunca removem um fundo.",
    ).font = _NOTE_FONT
    if block["baseline_warning"]:
        note_row += 1
        sheet.cell(
            row=note_row, column=1, value=f"Atencao: {block['baseline_warning']}."
        ).font = _ALERT_FONT
    if block["excluded"]:
        note_row += 1
        out = "; ".join(f"{e['label']} ({e['reason']})" for e in block["excluded"])
        sheet.cell(
            row=note_row, column=1, value=f"Fora do universo: {out}."
        ).font = _NOTE_FONT
    sheet.freeze_panes = "A2"

    cat_sheet = workbook.create_sheet("Gaps por categoria")
    competitors = block["competitors"]
    cat_headers = (
        [("Categoria", 34), (f"{block['baseline_label']} (vitrine)", 20)]
        + [(c["label"], 16) for c in competitors]
        + [("Melhor concorrente", 24), ("Fundos de gap", 14)]
    )
    for index, (name, width) in enumerate(cat_headers, start=1):
        cat_sheet.cell(row=1, column=index, value=name)
        cat_sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(cat_sheet, 1, len(cat_headers))

    row = 1
    for entry in block["categories"]:
        row += 1
        best = entry["best_competitor"]
        values = (
            [entry["category"], entry["xp"]]
            + [entry["by_platform"].get(c["platform_id"], 0) for c in competitors]
            + [
                f"{best['label']} ({best['count']})" if best else "-",
                entry["gap_count"],
            ]
        )
        for index, value in enumerate(values, start=1):
            cell = cat_sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
    if row == 1:
        cat_sheet.cell(
            row=2, column=1, value="Nenhum gap por categoria nesta execucao."
        ).font = _NOTE_FONT
    cat_sheet.cell(
        row=row + 2, column=1,
        value="Contagens por vitrine (varejo) de cada casa na categoria; "
        "'Fundos de gap' = quantos gaps da aba 'Gaps XP' caem na categoria. "
        "Categorias ordenadas por gaps; alem das primeiras, agregadas em 'Outras'.",
    ).font = _NOTE_FONT
    cat_sheet.freeze_panes = "A2"


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


def _periodos_das_abas(
    retail_flow: dict | None,
    performance: dict | None,
    rentabilidade: dict | None,
    custo: dict | None,
    mandato: dict | None,
) -> list[tuple[str, str, str]]:
    """(aba, fonte, periodo) de cada bloco que tem periodo proprio.

    A planilha mistura blocos de janelas diferentes na mesma pasta - captacao de
    tres meses ao lado de rentabilidade de vinte e quatro e custo que nao e
    intervalo nenhum. Sem esta tabela, quem abre uma aba no meio nao tem como
    saber sobre que periodo o numero fala.
    """
    linhas: list[tuple[str, str, str]] = []

    if retail_flow and retail_flow.get("available"):
        periodo = retail_flow["periodo"]
        competencias = periodo.get("meses_lidos") or []
        detalhe = f"{periodo.get('de')} a {periodo.get('ate')}"
        if competencias:
            detalhe += f" ({len(competencias)} competencias: {', '.join(competencias)})"
        ausentes = periodo.get("meses_ausentes") or []
        if ausentes:
            detalhe += f" - ausentes: {', '.join(ausentes)}"
        for aba in ("Captacao por fundo", "Tendencia de captacao", "Oportunidades de grade"):
            linhas.append((aba, "Informe diario da CVM (INF_DIARIO)", detalhe))

    if performance and performance.get("available"):
        periodo = performance["periodo"]
        detalhe = (
            f"{periodo.get('de')} a {periodo.get('ate')} "
            f"({periodo.get('pregoes')} pregoes)"
        )
        for aba in ("Rentabilidade ponderada", "Onde esta a gente"):
            linhas.append((aba, "Cota patrimonial do INF_DIARIO", detalhe))

    if rentabilidade and rentabilidade.get("available"):
        janelas = "; ".join(
            f"{j['rotulo']}: {j['de']} a {j['ate']}"
            for j in rentabilidade.get("janelas") or []
        )
        linhas.append(
            ("Rentabilidade por fundo", "Cota patrimonial do INF_DIARIO", janelas)
        )

    if all(
        bloco and bloco.get("available")
        for bloco in (retail_flow, performance, rentabilidade)
    ):
        linhas.append((
            "Por que 3 meses aparece com dois valores",
            "Base do retorno",
            "'Captacao por fundo' e 'Rentabilidade ponderada' usam a cota do "
            "PRIMEIRO PREGAO da janela, porque leem so as competencias da janela. "
            "'Rentabilidade por fundo' usa a cota do FIM DA COMPETENCIA ANTERIOR, "
            "que e a convencao de lamina e a fonte tem historico para isso. A "
            "diferenca e o primeiro pregao do periodo: medido em 2026-09-20, o "
            "Mapfre Confianza sai com 2,99% numa base e 3,05% na outra, nos mesmos "
            "tres meses.",
        ))

    if custo and custo.get("available"):
        idade = (custo.get("universo") or {}).get("idade_maxima_meses")
        linhas.append((
            "Custo e acesso",
            "Extrato de informacoes da CVM (EXTRATO)",
            f"Nao e intervalo: cada classe tem a data-base da propria entrega, "
            f"e so entram as de ate {idade} meses",
        ))

    if mandato and mandato.get("available"):
        linhas.append((
            "Mandato declarado",
            "Extrato de informacoes da CVM (EXTRATO)",
            "Nao se aplica: condicao vigente do regulamento, nao medida de periodo",
        ))

    return linhas


def _add_methodology_sheet(
    workbook: Workbook,
    snapshots: list[ShelfSnapshot],
    enrichment: EnrichmentStats | None,
    generated_at: datetime,
    periodos: list[tuple[str, str, str]] | None = None,
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
    if periodos:
        row += 1
        sheet.cell(row=row, column=1, value="Periodo de cada aba").font = _TITLE_FONT
        row += 1
        sheet.cell(row=row, column=1, value="Aba").font = _HEADER_FONT
        sheet.cell(row=row, column=1).fill = _HEADER_FILL
        sheet.cell(row=row, column=2, value="Fonte e periodo").font = _HEADER_FONT
        sheet.cell(row=row, column=2).fill = _HEADER_FILL
        row += 1
        for aba, fonte, detalhe in periodos:
            sheet.cell(row=row, column=1, value=aba).font = _BODY_FONT
            sheet.cell(row=row, column=2, value=f"{fonte} - {detalhe}").font = _BODY_FONT
            row += 1
        row += 1
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
        row, "Gaps XP",
        "Gap = fundo de varejo na vitrine de 1+ concorrente e fora da vitrine "
        "da XP, por CNPJ. Vitrine x vitrine: o catalogo Open Finance nao conta "
        "em nenhum dos lados. XP indisponivel ou sem vitrine suprime a analise; "
        "XP parcial gera aviso; concorrente indisponivel ou sem vitrine sai do "
        "universo com motivo. 'Gestora na vitrine XP' e 'Possivel equivalente "
        "XP' sao heuristicas informativas - nunca removem um fundo da lista.",
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
        if enrichment.classified_by_name:
            vias = ", ".join(
                f"{via} {n}"
                for via, n in sorted(
                    enrichment.by_name_via.items(), key=lambda kv: -kv[1]
                )
            )
            row = line(
                row, "Classificacao por nome",
                f"{enrichment.classified_by_name} fundos que o join por "
                "identidade nao alcancou receberam a categoria oficial pelo "
                "nome, casando contra o cadastro da CVM por prefixo de slug "
                "mais taxa, aplicacao minima e prazo de resgate. E o que "
                "coloca na comparacao a casa que nao publica CNPJ - a vitrine "
                "do Itau traz CNPJ em 6% dos fundos. Nenhuma dessas vias "
                "preenche CNPJ: elas resolvem a CATEGORIA, nao a identidade, e "
                "a via de cada fundo fica na coluna categoria_via. "
                f"Vias usadas: {vias}.",
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
    retail_flow: dict | None = None,
    grade: dict | None = None,
    performance: dict | None = None,
    custo: dict | None = None,
    mandato: dict | None = None,
    rentabilidade: dict | None = None,
) -> Path:
    """Gera a planilha das prateleiras. Devolve o caminho do arquivo."""
    generated_at = generated_at or datetime.now()
    usable = [s for s in snapshots if s.funds]

    workbook = Workbook()
    last_row = _add_consolidated(workbook, usable)
    listing_last_row = _add_listing_sheet(workbook, usable)
    # Gaps recebem os snapshots completos: o proprio bloco decide quem entra
    # no universo e lista os excluidos com motivo.
    _add_gaps_sheets(workbook, snapshots, listing_last_row)
    _add_restricted_sheet(workbook, usable)
    if diffs:
        _add_movements_sheet(workbook, diffs)
    _add_platform_sheets(workbook, usable)
    if cvm and cvm.funds:
        _add_cvm(workbook, cvm)
    _add_source_coverage(workbook, usable)
    if retail_flow and retail_flow.get("available"):
        _add_flow_sheet(workbook, retail_flow)
        _add_trend_sheet(workbook, retail_flow)
    if grade and grade.get("available"):
        _add_grade_sheet(workbook, grade)
    if rentabilidade and rentabilidade.get("available"):
        _add_rentabilidade_sheet(workbook, rentabilidade)
    if performance and performance.get("available"):
        _add_performance_sheet(workbook, performance)
        _add_gente_sheet(workbook, performance)
    if custo and custo.get("available"):
        _add_custo_sheet(workbook, custo)
    if mandato and mandato.get("available"):
        _add_mandato_sheet(workbook, mandato)
    _add_collection_health(workbook, snapshots, cvm)
    _add_methodology_sheet(
        workbook, snapshots, enrichment, generated_at,
        periodos=_periodos_das_abas(
            retail_flow, performance, rentabilidade, custo, mandato
        ),
    )
    _add_summary(workbook, usable, snapshots, last_row, generated_at, listing_last_row)

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = path or (
        config.reports_dir / f"prateleiras-{generated_at.date().isoformat()}.xlsx"
    )
    return _salvar(workbook, path)


def _salvar(workbook: Workbook, path: Path) -> Path:
    """Grava a planilha, caindo para um nome alternativo se o alvo estiver preso.

    O Excel mantem o arquivo aberto travado para escrita (e deixa um `~$...` ao
    lado). Uma execucao que levou dezenas de minutos nao pode terminar sem
    planilha porque alguem estava com a anterior aberta - e foi o que aconteceu
    em 2026-09-20, com o `run` completo devolvendo "Permission denied" no ultimo
    passo. Aqui a coleta e preservada num arquivo vizinho, e quem chamou recebe
    o caminho que de fato existe.
    """
    try:
        workbook.save(path)
        return path
    except PermissionError:
        for sufixo in range(2, 10):
            alternativo = path.with_name(f"{path.stem}-{sufixo}{path.suffix}")
            try:
                workbook.save(alternativo)
                return alternativo
            except PermissionError:
                continue
        raise


# --- captacao ----------------------------------------------------------------

_FLOW_HEADERS = [
    "Fundo", "CNPJ", "Classe", "Gestora", "Vitrines", "Exclusivo",
    "Captacao bruta (R$)", "Resgates (R$)", "Captacao liquida (R$)",
    "PL inicio (R$)", "PL fim (R$)", "Captacao liquida / PL inicio (%)",
    "Cotistas inicio", "Cotistas fim", "Variacao cotistas",
    "Entrada por cotista ganho (R$)",
    "Rentabilidade no periodo, base no 1o pregao (%)",
    "Pregoes observados", "Tendencia", "Movimento estrutural",
]

# Colunas por tipo, para formatar sem numero magico espalhado pelo laco.
_FLOW_MONEY = (7, 8, 9, 10, 11, 16)
_FLOW_PCT = (12, 17)
_FLOW_INT = (13, 14, 15, 18)
_FLOW_LIQUIDA = 9


def _flow_row(fundo: dict) -> list:
    return [
        fundo["nome"],
        format_cnpj(fundo["cnpj"]),
        fundo["categoria"],
        fundo.get("gestora") or "",
        ", ".join(fundo["vitrines"]),
        "sim" if fundo["exclusivo"] else "",
        fundo["captacao_bruta"],
        fundo["resgates"],
        fundo["captacao_liquida"],
        fundo["pl_inicio"],
        fundo["pl_fim"],
        # _PCT concatena o "%" ao valor: a planilha guarda pontos percentuais
        # (1,24 para 1,24%), nao a fracao - mesma convencao da taxa de adm.
        fundo.get("captacao_sobre_pl"),
        fundo.get("cotistas_inicio"),
        fundo["cotistas_fim"],
        fundo["variacao_cotistas"],
        fundo.get("ticket_por_cotista"),
        fundo.get("rentabilidade"),
        fundo.get("dias"),
        fundo.get("tendencia") or "",
        "sim" if fundo.get("estrutural") else "",
    ]


def _write_flow_rows(ws, fundos: list[dict], header_row: int) -> int:
    for col_idx, header in enumerate(_FLOW_HEADERS, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col_idx)].width = (
            46 if col_idx == 1 else 20 if col_idx in (2, 4) else 16
        )
    ws.row_dimensions[header_row].height = 30

    row_idx = header_row + 1
    for fundo in fundos:
        for col_idx, valor in enumerate(_flow_row(fundo), start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=valor)
            cell.font = _BODY_FONT
            if col_idx in _FLOW_MONEY:
                cell.number_format = _MONEY
            elif col_idx in _FLOW_PCT:
                cell.number_format = _PCT
            elif col_idx in _FLOW_INT:
                cell.number_format = _INT
            if col_idx == _FLOW_LIQUIDA and isinstance(valor, (int, float)) and valor:
                cell.fill = _GREEN_FILL if valor > 0 else _RED_FILL
        row_idx += 1
    return row_idx


# Rotulos de tendencia, para a legenda da aba. Mesmos textos de
# retail_flow.TREND_LABELS, repetidos aqui so quando aquele nao estiver
# disponivel - a importacao e opcional para a planilha nao depender do bloco.
_TENDENCIA_FALLBACK = {
    "acelerando": "captando mais por dia que nos meses anteriores",
    "desacelerando": "ainda captando, mas menos por dia que antes",
    "virou_entrada": "estava perdendo dinheiro e passou a captar",
    "virou_saida": "estava captando e passou a perder dinheiro",
    "saida_persistente": "perdendo no ultimo mes e na media anterior",
    "estavel": "sem mudanca material no ritmo",
    "sem_serie": "competencias de menos para falar em tendencia",
}


def _periodo_bloco(
    sheet: Worksheet,
    linha: int,
    *,
    de: str | None,
    ate: str | None,
    competencias: list[str] | None = None,
    ausentes: list[str] | None = None,
    fonte: str = "",
    nota: str = "",
) -> int:
    """Escreve o periodo de um bloco em campos rotulados, nao em prosa.

    Existe porque "o periodo" estava so na frase de subtitulo de cada aba, e uma
    planilha que mistura captacao de tres meses com rentabilidade de vinte e
    quatro precisa dizer, em cada bloco, exatamente sobre o que o numero fala -
    inclusive quais competencias faltaram, que e o que explica um total menor do
    que o esperado.
    """
    def campo(rotulo: str, valor: str) -> None:
        nonlocal linha
        sheet.cell(row=linha, column=1, value=rotulo).font = _HEADER_FONT
        sheet.cell(row=linha, column=2, value=valor).font = _BODY_FONT
        linha += 1

    if fonte:
        campo("Fonte", fonte)
    campo("Periodo", f"{de} a {ate}" if de and ate else "nao informado")
    if competencias:
        campo(
            "Competencias",
            f"{len(competencias)} ({competencias[0]} a {competencias[-1]})",
        )
    if ausentes:
        campo("Competencias ausentes", ", ".join(ausentes))
    if nota:
        sheet.cell(row=linha, column=1, value=nota).font = _NOTE_FONT
        linha += 1
    return linha + 1


def _add_flow_sheet(workbook: Workbook, flow: dict) -> None:
    """Aba "Captacao por fundo": o fluxo de CADA fundo de vitrine no periodo.

    Ate 2026-09-20 esta aba gravava apenas `top_captacao + top_resgate +
    estruturais` - 38 fundos de mais de mil. O docstring ja dizia que "quem abre
    a planilha para conferir um numero precisa encontrar o fundo que procurou";
    a implementacao nao cumpria. Agora entra a lista inteira, ordenada por
    captacao liquida, e os rankings continuam existindo no digest e no
    dashboard, que sao as superficies de leitura rapida.

    Movimento estrutural entra marcado na ultima coluna em vez de ser omitido,
    pelo mesmo motivo: some-lo esconderia justamente o fundo cujo numero
    estranho levou alguem a abrir a planilha.
    """
    ws = workbook.create_sheet("Captacao por fundo")
    periodo = flow["periodo"]
    competencias = periodo.get("meses_lidos") or []

    ws.cell(row=1, column=1, value="Captacao dos fundos de vitrine").font = _TITLE_FONT
    _periodo_bloco(
        ws, 2,
        de=periodo.get("de"), ate=periodo.get("ate"),
        competencias=competencias,
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Informe diario da CVM (INF_DIARIO)",
        nota=(
            "A CVM publica fluxo por FUNDO, nunca por distribuidor: um fundo em "
            "tres vitrines entra inteiro nas tres somas. A coluna Exclusivo marca "
            "onde a atribuicao a uma casa e inequivoca. Uma linha do informe e uma "
            "SUBCLASSE: PL, cotistas e fluxo somam as subclasses da classe, e a "
            "rentabilidade e a media dos retornos delas ponderada pelo PL. "
            "Rentabilidade e da cota patrimonial, ex-provento, com base no PRIMEIRO "
            "PREGAO do periodo - a aba 'Rentabilidade por fundo' usa a cota do fim "
            "da competencia anterior, que e a convencao de lamina, e por isso o "
            "mesmo fundo aparece com numero ligeiramente diferente nas duas."
        ),
    )

    fundos = sorted(
        flow.get("fundos") or [],
        key=lambda f: -(f.get("captacao_liquida") or 0),
    )
    if not fundos:  # base antiga, sem a lista completa: cai nos rankings
        vistos: set[str] = set()
        for fundo in sorted(
            flow["top_captacao"] + flow["top_resgate"] + flow.get("estruturais", []),
            key=lambda f: -abs(f["captacao_liquida"]),
        ):
            if fundo["cnpj"] not in vistos:
                vistos.add(fundo["cnpj"])
                fundos.append(fundo)

    cabecalho = 7
    ultima = _write_flow_rows(ws, fundos, cabecalho)
    ws.freeze_panes = f"A{cabecalho + 1}"
    if ultima > cabecalho + 1:
        ws.auto_filter.ref = f"A{cabecalho}:T{ultima - 1}"

    linha = ultima + 1
    ws.cell(row=linha, column=1, value="Legenda da coluna Tendencia").font = _HEADER_FONT
    linha += 1
    rotulos = flow.get("tendencia_labels") or _TENDENCIA_FALLBACK
    for chave, texto in rotulos.items():
        ws.cell(row=linha, column=1, value=chave).font = _BODY_FONT
        ws.cell(row=linha, column=2, value=texto).font = _NOTE_FONT
        linha += 1


def _add_trend_sheet(workbook: Workbook, flow: dict) -> None:
    """Aba "Tendencia de captacao": a serie mensal, por casa e por fundo.

    A coluna que importa e a media diaria, nao o total do mes: o mes corrente
    fecha com menos pregoes que os cheios, e comparar totais marcaria todo fundo
    como desacelerando por um efeito de calendario.
    """
    ws = workbook.create_sheet("Tendencia de captacao")
    competencias = flow.get("competencias") or []

    ws.cell(row=1, column=1, value="Tendencia de captacao").font = _TITLE_FONT
    ws.cell(
        row=2, column=1,
        value=(
            "Media diaria de captacao liquida por competencia. A media diaria "
            "existe porque o mes corrente tem menos pregoes que os fechados - "
            "comparar totais brutos seria comparar calendarios."
        ),
    ).font = _NOTE_FONT

    linha = 4
    ws.cell(row=linha, column=1, value="Por vitrine").font = _HEADER_FONT
    linha += 1
    cabecalho = ["Casa"] + [f"{c[4:]}/{c[:4]}" for c in competencias] + ["Dias observados"]
    for col_idx, header in enumerate(cabecalho, start=1):
        cell = ws.cell(row=linha, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        ws.column_dimensions[get_column_letter(col_idx)].width = 26 if col_idx == 1 else 18
    linha += 1

    for platform_id, serie in (flow.get("serie_por_casa") or {}).items():
        por_competencia = {m["competencia"]: m for m in serie}
        ws.cell(row=linha, column=1, value=_platform_label(platform_id)).font = _BODY_FONT
        for idx, competencia in enumerate(competencias, start=2):
            mes = por_competencia.get(competencia)
            cell = ws.cell(
                row=linha, column=idx,
                value=mes["media_diaria"] if mes else None,
            )
            cell.font = _BODY_FONT
            cell.number_format = _MONEY
            if mes and mes["media_diaria"]:
                cell.fill = _GREEN_FILL if mes["media_diaria"] > 0 else _RED_FILL
        ws.cell(
            row=linha, column=len(competencias) + 2,
            value=", ".join(str(m["dias"]) for m in serie),
        ).font = _NOTE_FONT
        linha += 1

    linha += 2
    resumo = flow.get("tendencias") or {}
    rotulos = flow.get("tendencia_labels") or {}
    ws.cell(row=linha, column=1, value="Fundos por tendencia").font = _HEADER_FONT
    linha += 1
    for chave, quantidade in resumo.items():
        ws.cell(row=linha, column=1, value=chave).font = _BODY_FONT
        ws.cell(row=linha, column=2, value=quantidade).font = _BODY_FONT
        ws.cell(row=linha, column=3, value=rotulos.get(chave, "")).font = _NOTE_FONT
        linha += 1

    for titulo, chave in (
        ("Acelerando - captando mais por dia que antes", "acelerando"),
        ("Viraram saida - captavam e passaram a perder", "virou_saida"),
    ):
        fundos = flow.get(chave) or []
        if not fundos:
            continue
        linha += 2
        ws.cell(row=linha, column=1, value=titulo).font = _HEADER_FONT
        linha += 1
        cabecalho = ["Fundo", "Vitrines"] + [f"{c[4:]}/{c[:4]}" for c in competencias] + ["Total (R$)"]
        for col_idx, header in enumerate(cabecalho, start=1):
            cell = ws.cell(row=linha, column=col_idx, value=header)
            cell.fill = _HEADER_FILL
            cell.font = _HEADER_FONT
        linha += 1
        for fundo in fundos:
            por_competencia = {m["competencia"]: m for m in fundo.get("meses", [])}
            ws.cell(row=linha, column=1, value=fundo["nome"]).font = _BODY_FONT
            ws.cell(row=linha, column=2, value=", ".join(fundo["vitrines"])).font = _BODY_FONT
            for idx, competencia in enumerate(competencias, start=3):
                mes = por_competencia.get(competencia)
                cell = ws.cell(
                    row=linha, column=idx,
                    value=mes["media_diaria"] if mes else None,
                )
                cell.font = _BODY_FONT
                cell.number_format = _MONEY
            total = ws.cell(
                row=linha, column=len(competencias) + 3,
                value=fundo["captacao_liquida"],
            )
            total.font = _BODY_FONT
            total.number_format = _MONEY
            linha += 1


def _add_grade_sheet(workbook: Workbook, grade: dict) -> None:
    """Aba "Oportunidades de grade": o que capta fora da vitrine da XP."""
    ws = workbook.create_sheet("Oportunidades de grade")

    ws.cell(row=1, column=1, value="Oportunidades de grade para a XP").font = _TITLE_FONT
    ws.cell(
        row=2, column=1,
        value=(
            f"{grade['total']} fundos captam varejo fora da vitrine da XP; "
            f"{grade['total_produto_do_concorrente']} sao produto proprio da casa que "
            f"os exibe e nao se transferem. Restam {grade['total_transferiveis']} "
            f"({grade['total_operacionais']} de gestora ja distribuida pela XP, "
            f"{grade['total_comerciais']} exigem abrir gestora)."
        ),
    ).font = _NOTE_FONT

    linha = 4
    for titulo, chave in (
        ("Inclusao operacional - gestora ja distribuida pela XP", "operacionais"),
        ("Exige abrir gestora", "comerciais"),
        ("Mais varejo - por cotistas trazidos", "mais_varejo"),
        ("Produto proprio do concorrente - nao transferivel", "produto_do_concorrente"),
        ("Na vitrine da XP e perdendo varejo", "na_vitrine_perdendo"),
    ):
        fundos = grade.get(chave) or []
        if not fundos:
            continue
        ws.cell(row=linha, column=1, value=titulo).font = _HEADER_FONT
        linha = _write_flow_rows(ws, fundos, linha + 1) + 1


# --- rentabilidade ponderada --------------------------------------------------

_PERF_HEADERS = [
    "Casa", "Categoria", "Fundos", "Mediana (%)", "Por PL (%)",
    "Por cotista (%)", "Gap PL-cotista (pp)", "Mediana da categoria no mercado (%)",
    "PL (R$)", "Cotistas",
]

_GENTE_HEADERS = [
    "Casa", "Fundo", "CNPJ", "Categoria", "Cotistas", "PL (R$)",
    "Ticket medio (R$)", "Rentabilidade (%)", "Mediana da categoria (%)",
    "Excesso (pp)", "Captacao liquida (R$)", "Variacao cotistas", "Pregoes",
]


def _add_performance_sheet(workbook: Workbook, performance: dict) -> None:
    """Aba "Rentabilidade ponderada": as tres leituras, por casa e por categoria.

    A tabela de cima responde pela vitrine inteira; a de baixo, dentro de cada
    categoria - e e nela que o gap fica legivel, porque na vitrine inteira a
    diferenca entre as ponderadas mistura composicao com desempenho.
    """
    ws = workbook.create_sheet("Rentabilidade ponderada")
    periodo = performance["periodo"]
    universo = performance["universo"]

    ws.cell(row=1, column=1, value="Rentabilidade da vitrine").font = _TITLE_FONT
    linha = _periodo_bloco(
        ws, 2,
        de=periodo.get("de"), ate=periodo.get("ate"),
        competencias=periodo.get("meses_lidos") or [],
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Cota patrimonial do informe diario da CVM (INF_DIARIO)",
        nota=(
            f"{periodo.get('pregoes')} pregoes. Referencia: mediana da mesma "
            f"categoria no universo oficial ({universo['fundos_com_serie']} fundos, "
            f"{universo['categorias_com_referencia']} categorias com amostra "
            f">= {universo['amostra_minima']}). E o retorno dos fundos que a casa "
            "exibe, com o peso que eles tem no MERCADO - nao o retorno dos clientes "
            "dela: a CVM publica cota, PL e cotistas por fundo, somando todos os "
            "distribuidores. Cota ex-provento. INDICE = share do PL nos fundos "
            "abaixo da mediana dividido pelo share de fundos abaixo: abaixo de 1, o "
            "dinheiro EVITA os piores da categoria; acima de 1, se concentra neles. "
            "Cerca de metade de qualquer universo fica abaixo da mediana por "
            "construcao, entao a contagem de fundos sozinha nao e sinal de nada."
        ),
    )
    resumo = [
        "Casa", "Fundos com serie", "Vitrine", "Cobertura",
        "Mediana (%)", "Por PL (%)", "Por cotista (%)", "Gap (pp)",
        "Fundos abaixo da mediana", "% dos fundos", "% do PL", "% dos cotistas",
        "Indice PL", "Indice cotistas",
    ]
    for col, titulo in enumerate(resumo, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(resumo))
    linha += 1
    for casa in performance["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        valores = [
            casa["label"], casa["com_serie"], casa["vitrine"], casa["cobertura"],
            casa["mediana"], casa["por_pl"], casa["por_cotista"], casa["gap"],
            f"{massa['fundos']} de {massa['de']}",
            massa["share_fundos"], massa["share_pl"], massa["share_cotistas"],
            massa["indice_pl"], massa["indice_cotistas"],
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=linha, column=col, value=valor)
        for col in (5, 6, 7, 8):
            ws.cell(row=linha, column=col).number_format = _PCT
        for col in (4, 10, 11, 12):
            ws.cell(row=linha, column=col).number_format = '0.0%;-;-'
        for col in (13, 14):
            ws.cell(row=linha, column=col).number_format = _INDICE_FMT
        _style_row(ws, linha, len(resumo))
        linha += 1

    linha += 1
    ws.cell(row=linha, column=1, value="Por categoria").font = _TITLE_FONT
    linha += 1
    for col, titulo in enumerate(_PERF_HEADERS, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(_PERF_HEADERS))
    cabecalho = linha
    linha += 1
    for item in performance["por_categoria"]:
        valores = [
            item["label"], item["categoria"], item["fundos"], item["mediana"],
            item["por_pl"], item["por_cotista"], item["gap"], item["referencia"],
            item["pl"], item["cotistas"],
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=linha, column=col, value=valor)
        for col in (4, 5, 6, 7, 8):
            ws.cell(row=linha, column=col).number_format = _PCT
        ws.cell(row=linha, column=9).number_format = _MONEY
        ws.cell(row=linha, column=10).number_format = _INT
        _style_row(ws, linha, len(_PERF_HEADERS))
        linha += 1

    ws.freeze_panes = f"A{cabecalho + 1}"
    if linha > cabecalho + 1:
        ws.auto_filter.ref = f"A{cabecalho}:J{linha - 1}"
    for col, largura in zip("ABCDEFGHIJK", (22, 40, 9, 12, 12, 14, 18, 26, 18, 12, 18)):
        ws.column_dimensions[col].width = largura


def _add_gente_sheet(workbook: Workbook, performance: dict) -> None:
    """Aba "Onde esta a gente": os fundos com mais cotistas, e como rendem.

    Ordenada por numero de cotistas, nao por PL nem por retorno: a duvida que
    ela responde nao e sobre o fundo medio da prateleira, e sim sobre aquele em
    que a maior parte das pessoas de fato esta.
    """
    ws = workbook.create_sheet("Onde esta a gente")
    periodo = performance.get("periodo") or {}
    ws.cell(row=1, column=1, value="Onde esta a gente").font = _TITLE_FONT
    linha = _periodo_bloco(
        ws, 2,
        de=periodo.get("de"), ate=periodo.get("ate"),
        competencias=periodo.get("meses_lidos") or [],
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Cota patrimonial do informe diario da CVM (INF_DIARIO)",
        nota=(
            "Os fundos de cada vitrine com mais cotistas, com o retorno do periodo "
            "ao lado da mediana da mesma categoria no mercado. Excesso negativo e "
            "um fundo de muita gente rendendo abaixo dos pares. Cotistas e PL sao "
            "do fundo inteiro, somando todos os distribuidores."
        ),
    )
    for col, titulo in enumerate(_GENTE_HEADERS, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(_GENTE_HEADERS))
    cabecalho_gente = linha
    linha += 1

    rotulos = {c["platform_id"]: c["label"] for c in performance["casas"]}
    for platform_id, fundos in performance["onde_esta_a_gente"].items():
        for fundo in fundos:
            valores = [
                rotulos.get(platform_id, platform_id), fundo["nome"],
                format_cnpj(fundo["cnpj"]), fundo["categoria"], fundo["cotistas"],
                fundo["pl"], fundo["ticket_medio"], fundo["retorno"],
                fundo["mediana_categoria"], fundo["excesso"],
                fundo["captacao_liquida"], fundo["variacao_cotistas"], fundo["dias"],
            ]
            for col, valor in enumerate(valores, start=1):
                ws.cell(row=linha, column=col, value=valor)
            ws.cell(row=linha, column=3).alignment = Alignment(horizontal="left")
            for col in (5, 12, 13):
                ws.cell(row=linha, column=col).number_format = _INT
            for col in (6, 7, 11):
                ws.cell(row=linha, column=col).number_format = _MONEY
            for col in (8, 9, 10):
                ws.cell(row=linha, column=col).number_format = _PCT
            _style_row(ws, linha, len(_GENTE_HEADERS))
            linha += 1

    ws.freeze_panes = f"A{cabecalho_gente + 1}"
    if linha > cabecalho_gente + 1:
        ws.auto_filter.ref = f"A{cabecalho_gente}:M{linha - 1}"
    for col, largura in zip(
        "ABCDEFGHIJKLM", (18, 48, 20, 34, 12, 18, 16, 14, 18, 12, 18, 14, 10)
    ):
        ws.column_dimensions[col].width = largura


# --- custo e mandato ----------------------------------------------------------

_CUSTO_HEADERS = [
    "Casa", "Fundo", "CNPJ", "Categoria", "Taxa adm (%)",
    "Mediana da categoria (%)", "Indice", "Faixa", "Idade da entrega (meses)",
    "Cobra entrada", "Cobra saida", "Custodia max (%)", "Dias de cotizacao",
    "Minimo da classe (R$)", "Menor porta do mesmo fundo (R$)",
    "Fora do indice porque",
]

_INDICE_FMT = '0.00"x";-;-'
_SHARE_FMT = '0.0%;-;-'


def _add_custo_sheet(workbook: Workbook, custo: dict) -> None:
    """Aba "Custo e acesso": a taxa de cada fundo contra a categoria dele.

    A coluna que importa e Indice, nao Taxa: comparar a taxa bruta entre casas
    mede o MIX da prateleira (renda fixa custa ~0,2% ao ano, acoes ~2%), nao o
    preco dos produtos. Os fundos que ficaram fora do indice aparecem com o
    motivo na ultima coluna, em vez de sumirem da tabela.
    """
    ws = workbook.create_sheet("Custo e acesso")
    universo = custo["universo"]

    ws.cell(row=1, column=1, value="Custo da vitrine").font = _TITLE_FONT
    ws.cell(row=2, column=1, value="Fonte").font = _HEADER_FONT
    ws.cell(
        row=2, column=2,
        value="Extrato de informacoes da CVM (EXTRATO) - uma entrega por classe",
    ).font = _BODY_FONT
    ws.cell(row=3, column=1, value="Periodo").font = _HEADER_FONT
    ws.cell(
        row=3, column=2,
        value=(
            "Nao e um intervalo: cada classe tem a data-base da propria entrega, "
            f"e so entram as de ate {universo['idade_maxima_meses']} meses. A "
            "coluna 'Idade da entrega' diz a de cada fundo."
        ),
    ).font = _BODY_FONT
    ws.cell(
        row=4, column=1,
        value=(
            f"Extrato de informacoes da CVM: {universo['classes_na_base']} classes, "
            f"{universo['categorias_com_referencia']} categorias com amostra "
            f">= {universo['amostra_minima']} para servir de referencia. Entram so "
            "taxas em percentual ao ano (o mesmo campo carrega valor em reais e "
            f"texto livre) com entrega de ate {universo['idade_maxima_meses']} "
            "meses. Taxa e propriedade da classe, identica em qualquer "
            "distribuidor - por isso pode vir da base oficial. Aplicacao minima "
            "NAO: o minimo do registro e o institucional da classe, nao o ticket "
            "que o site cobra."
        ),
    ).font = _NOTE_FONT

    linha = 6
    resumo = [
        "Casa", "Comparaveis", "Vitrine", "Indice mediano", "Caros", "Baratos",
        "Sem taxa de entrada/saida", "Resgate ate D+1", "Com porta menor",
    ]
    for col, titulo in enumerate(resumo, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(resumo))
    linha += 1
    for casa in custo["casas"]:
        valores = [
            casa["label"], casa["comparaveis"], casa["vitrine"],
            casa["indice_mediano"], casa["share_caro"], casa["share_barato"],
            casa["share_sem_entrada_ou_saida"], casa["share_liquidez_imediata"],
            casa["com_porta_menor"],
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=linha, column=col, value=valor)
        ws.cell(row=linha, column=4).number_format = _INDICE_FMT
        for col in (5, 6, 7, 8):
            ws.cell(row=linha, column=col).number_format = _SHARE_FMT
        _style_row(ws, linha, len(resumo))
        linha += 1

    linha += 1
    ws.cell(
        row=linha, column=1, value="Fundos mais caros e portas de entrada menores"
    ).font = _TITLE_FONT
    linha += 1
    for col, titulo in enumerate(_CUSTO_HEADERS, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(_CUSTO_HEADERS))
    cabecalho = linha
    linha += 1

    rotulos = {c["platform_id"]: c["label"] for c in custo["casas"]}
    listados: list[tuple[str, dict]] = [("", f) for f in custo["mais_caros"]]
    for platform_id, fundos in (custo.get("porta_menor") or {}).items():
        listados += [(platform_id, f) for f in fundos]

    vistos: set[str] = set()
    for platform_id, fundo in listados:
        if fundo["cnpj"] in vistos:
            continue
        vistos.add(fundo["cnpj"])
        valores = [
            rotulos.get(platform_id, ""), fundo["nome"],
            format_cnpj(fundo["cnpj"]), fundo["categoria"], fundo["taxa_adm"],
            fundo["mediana_categoria"], fundo["indice"], fundo["faixa"],
            fundo["idade_meses"], fundo["tem_taxa_entrada"],
            fundo["tem_taxa_saida"], fundo["taxa_custodia_max"],
            fundo["dias_cotizacao"], fundo["minimo_classe"], fundo["porta_menor"],
            fundo["motivo_fora"],
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=linha, column=col, value=valor)
        ws.cell(row=linha, column=3).alignment = Alignment(horizontal="left")
        for col in (5, 6, 12):
            ws.cell(row=linha, column=col).number_format = _PCT
        ws.cell(row=linha, column=7).number_format = _INDICE_FMT
        for col in (9, 13):
            ws.cell(row=linha, column=col).number_format = _INT
        for col in (14, 15):
            ws.cell(row=linha, column=col).number_format = _MONEY
        _style_row(ws, linha, len(_CUSTO_HEADERS))
        linha += 1

    ws.freeze_panes = f"A{cabecalho + 1}"
    if linha > cabecalho + 1:
        ws.auto_filter.ref = f"A{cabecalho}:P{linha - 1}"
    for col, largura in zip(
        "ABCDEFGHIJKLMNOP",
        (16, 44, 20, 32, 12, 22, 10, 22, 20, 14, 14, 16, 16, 20, 26, 46),
    ):
        ws.column_dimensions[col].width = largura


def _add_mandato_sheet(workbook: Workbook, mandato: dict) -> None:
    """Aba "Mandato declarado": o que o regulamento de cada classe autoriza.

    Todo rotulo diz AUTORIZADO. Um fundo pode ter autorizacao para 100% de
    credito privado e carregar 10%; medir o que a carteira de fato tem exigiria
    a CDA, que nao esta no escopo.
    """
    ws = workbook.create_sheet("Mandato declarado")
    ws.cell(row=1, column=1, value="Mandato declarado").font = _TITLE_FONT
    ws.cell(row=2, column=1, value="Fonte").font = _HEADER_FONT
    ws.cell(
        row=2, column=2,
        value="Extrato de informacoes da CVM (EXTRATO) - politica de investimento",
    ).font = _BODY_FONT
    ws.cell(row=3, column=1, value="Periodo").font = _HEADER_FONT
    ws.cell(
        row=3, column=2,
        value=(
            "Nao se aplica: mandato e condicao vigente do regulamento, nao medida "
            "de um intervalo."
        ),
    ).font = _BODY_FONT
    ws.cell(row=4, column=1, value=mandato["nota"]).font = _NOTE_FONT

    linha = 6
    cabecalho = [
        "Casa", "Dimensao", "Share da vitrine", "Share das concorrentes",
        "Indice", "Aposta", "Teto mediano (%)",
    ]
    for col, titulo in enumerate(cabecalho, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(cabecalho))
    inicio = linha
    linha += 1
    for item in mandato["linhas"]:
        valores = [
            item["label"], item["rotulo"], item["share"], item["share_pares"],
            "so ela" if item["so_ela"] else item["indice"],
            item["aposta"], item["teto_mediano"],
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=linha, column=col, value=valor)
        for col in (3, 4):
            ws.cell(row=linha, column=col).number_format = _SHARE_FMT
        if not item["so_ela"]:
            ws.cell(row=linha, column=5).number_format = _INDICE_FMT
        ws.cell(row=linha, column=7).number_format = _PCT
        _style_row(ws, linha, len(cabecalho))
        linha += 1

    ws.freeze_panes = f"A{inicio + 1}"
    if linha > inicio + 1:
        ws.auto_filter.ref = f"A{inicio}:G{linha - 1}"
    for col, largura in zip("ABCDEFG", (18, 40, 18, 22, 12, 14, 18)):
        ws.column_dimensions[col].width = largura


# --- rentabilidade por fundo --------------------------------------------------


def _add_rentabilidade_sheet(workbook: Workbook, rent: dict) -> None:
    """Aba "Rentabilidade por fundo": o retorno de cada fundo em cada janela.

    A tabela de cima resume por casa; a de baixo tem uma linha por fundo, com
    todas as janelas lado a lado - que e o formato em que se responde "este
    fundo rendeu quanto no ano?" sem trocar de aba.

    Duas colunas existem para o numero poder ser auditado em vez de aceito:
    **Competencias na janela de 24m** mostra sobre quantos meses o retorno longo
    foi de fato calculado, e **Subclasses** avisa quando a classe tem mais de uma
    (o retorno e a media delas ponderada pelo PL, nao a de uma subclasse
    arbitraria).
    """
    ws = workbook.create_sheet("Rentabilidade por fundo")
    janelas = rent.get("janelas") or []
    rotulos = [j["rotulo"] for j in janelas]
    periodo = rent.get("periodo") or {}

    ws.cell(row=1, column=1, value="Rentabilidade por fundo").font = _TITLE_FONT
    linha = _periodo_bloco(
        ws, 2,
        de=periodo.get("de"), ate=periodo.get("ate"),
        fonte="Cota patrimonial do informe diario da CVM (INF_DIARIO)",
        nota=(
            "Retorno DO PERIODO de cada janela, nao anualizado - 28% em 24 meses "
            "e menos, ao ano, que 12% em 12. A base de cada janela e a cota do "
            "fim da competencia ANTERIOR a ela; quando o fundo nao existia la, e "
            "a primeira cota observada dentro da janela. Cota ex-provento: fundo "
            "que distribui aparece abaixo do que o investidor recebeu. PL e "
            "cotistas sao do fundo inteiro, somando todos os distribuidores. "
            "A aba 'Captacao por fundo' usa outra base - o primeiro pregao da "
            "janela - entao o mesmo fundo aparece la com numero ligeiramente "
            "diferente no mesmo intervalo."
        ),
    )

    ws.cell(row=linha, column=1, value="Janelas").font = _HEADER_FONT
    linha += 1
    for j in janelas:
        ws.cell(row=linha, column=1, value=j["rotulo"]).font = _BODY_FONT
        ws.cell(
            row=linha, column=2,
            value=(
                f"{j['de']} a {j['ate']} - "
                f"{j['competencias']} {'competencia' if j['competencias'] == 1 else 'competencias'}"
            ),
        ).font = _BODY_FONT
        linha += 1
    linha += 1

    # Resumo por casa: mediana, ponderada por PL e por cotistas em cada janela.
    resumo = ["Casa", "Fundos com serie", "Vitrine", "Leitura"] + rotulos
    for col, titulo in enumerate(resumo, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(resumo))
    linha += 1
    for casa in rent.get("casas") or []:
        for leitura, chave in (
            ("Fundo mediano", "mediana"),
            ("Real medio (por PL)", "por_pl"),
            ("Cotista medio", "por_cotista"),
            ("Gap PL - cotista (pp)", "gap"),
        ):
            valores = [casa["label"], casa["com_serie"], casa["vitrine"], leitura]
            for rotulo in rotulos:
                dados = (casa.get("janelas") or {}).get(rotulo)
                valores.append(dados.get(chave) if dados else None)
            for col, valor in enumerate(valores, start=1):
                ws.cell(row=linha, column=col, value=valor)
            for col in range(5, 5 + len(rotulos)):
                ws.cell(row=linha, column=col).number_format = (
                    '0.00"pp";-0.00"pp";-' if chave == "gap" else _PCT
                )
            _style_row(ws, linha, len(resumo))
            linha += 1

    linha += 1
    ws.cell(row=linha, column=1, value="Por fundo").font = _TITLE_FONT
    linha += 1

    cabecalho = (
        ["Fundo", "CNPJ", "Classe", "Gestora", "Vitrines", "PL (R$)", "Cotistas"]
        + [f"{r} (%)" for r in rotulos]
        + ["Subclasses", "Competencias na janela de 24m"]
    )
    for col, titulo in enumerate(cabecalho, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(cabecalho))
    topo = linha
    linha += 1

    longa = rotulos[-1] if rotulos else None
    for fundo in rent.get("fundos") or []:
        js = fundo.get("janelas") or {}
        valores = [
            fundo["nome"], format_cnpj(fundo["cnpj"]), fundo.get("categoria") or "",
            fundo.get("gestora") or "", ", ".join(fundo.get("vitrines") or []),
            fundo.get("pl"), fundo.get("cotistas"),
        ]
        valores += [(js.get(r) or {}).get("retorno") for r in rotulos]
        base = js.get(longa) or js.get(rotulos[0]) if rotulos else None
        valores += [
            (base or {}).get("subclasses"),
            (js.get(longa) or {}).get("meses") if longa else None,
        ]
        for col, valor in enumerate(valores, start=1):
            ws.cell(row=linha, column=col, value=valor)
        ws.cell(row=linha, column=2).alignment = Alignment(horizontal="left")
        ws.cell(row=linha, column=6).number_format = _MONEY
        ws.cell(row=linha, column=7).number_format = _INT
        for col in range(8, 8 + len(rotulos)):
            ws.cell(row=linha, column=col).number_format = _PCT
        for col in (8 + len(rotulos), 9 + len(rotulos)):
            ws.cell(row=linha, column=col).number_format = _INT
        _style_row(ws, linha, len(cabecalho))
        linha += 1

    ws.freeze_panes = f"A{topo + 1}"
    if linha > topo + 1:
        ws.auto_filter.ref = (
            f"A{topo}:{get_column_letter(len(cabecalho))}{linha - 1}"
        )
    larguras = [46, 20, 32, 28, 14, 18, 12] + [13] * len(rotulos) + [12, 26]
    for col, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(col)].width = largura

    for excluido in rent.get("excluded") or []:
        ws.cell(row=linha + 1, column=1, value=f"Fora: {excluido['label']}").font = _BODY_FONT
        ws.cell(row=linha + 1, column=2, value=excluido["reason"]).font = _NOTE_FONT
        linha += 1
