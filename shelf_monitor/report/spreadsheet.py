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
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from shelf_monitor.config import Config
from shelf_monitor.enrich import (
    EnrichmentStats,
    KEY_ANBIMA,
    KEY_CVM,
    KEY_SITUACAO,
)
from shelf_monitor.models import (
    BLOCO_CNPJ,
    BLOCO_COTISTAS,
    BLOCO_PRODUTO_PROPRIO,
    AssetClass,
    CvmSnapshot,
    FundOffer,
    RunReport,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import format_cnpj
from shelf_monitor.profile import split_by_profile
from shelf_monitor.report.retail_flow import (
    TREND_LABELS,
    leitura_da_captacao,
    nome_da_tendencia,
)
from shelf_monitor.distribuivel import ROTULOS_SITUACAO
from shelf_monitor.report.shared import ordem_canonica, rotulo_da_casa
from shelf_monitor.report.shared.nomes import nome_de_fundo
from shelf_monitor.report.shared.fontes import (
    CVM_EXTRATO,
    CVM_INFORME_DIARIO,
    Fonte,
    fontes_do_relatorio,
)
from shelf_monitor.report import captacao_na_xp as na_xp
from shelf_monitor.report.custo import nota_das_classes
from shelf_monitor.report.performance import (
    blocos_de_rentabilidade,
    com_rentabilidade,
    janela_do_bloco,
    prazo_das_colunas_de_rentabilidade,
    rentabilidade_por_cnpj,
)
from shelf_monitor.report.aggregate import (
    ASSET_CLASS_LABELS,
    COBERTURA_CARTEIRA,
    _offer_manager,
    build_gap_block,
    frase_do_funil,
    notas_da_cobertura,
    ranquear_gaps,
    is_own_product,
)
from shelf_monitor.texto import (
    competencia_br,
    contagem,
    data_br,
    milhar,
    periodo_br,
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
# Titulo de secao dentro de uma aba: negrito sem o fundo do cabecalho de tabela.
_HEADER_FONT_ESCURO = Font(name=FONT, size=11, bold=True)
_ALERT_FONT = Font(name=FONT, size=10, bold=True, color="C00000")
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(bottom=_THIN)

_MONEY = '#,##0;(#,##0);-'
_PCT = '0.00"%";-;-'
# Retorno e fluxo sobre o PL podem ser negativos, e o _PCT (feito para taxa,
# que nunca e) os mostraria como "-".
_PCT_RETORNO = '0.00"%";-0.00"%";0.00"%"'
_INT = '0;-;-'

# (cabecalho, largura, extrator) - a ordem aqui e a ordem das colunas.
_COLUMNS: list[tuple[str, int, object]] = [
    ("Plataforma", 20, None),  # preenchido pelo chamador
    ("Fundo", 52, lambda f: nome_de_fundo(f.name)),
    ("CNPJ", 20, lambda f: format_cnpj(f.cnpj) if f.cnpj else ""),
    ("Gestora", 34, lambda f: f.manager_name or ""),
    ("CNPJ da gestora", 20, lambda f: format_cnpj(f.manager_cnpj) if f.manager_cnpj else ""),
    ("Administrador", 30, lambda f: f.admin_name or ""),
    ("Classe", 16, lambda f: ASSET_CLASS_LABELS.get(f.asset_class, "Outros")),
    ("Categoria na origem", 30, lambda f: f.raw_category or ""),
    ("Aplicação mínima (R$)", 20, lambda f: f.min_initial),
    ("Taxa adm (%)", 14, lambda f: f.admin_fee_pct),
    ("Taxa perf (%)", 14, lambda f: f.performance_fee.amount_pct if f.performance_fee else None),
    ("Benchmark", 14, lambda f: (f.performance_fee.benchmark if f.performance_fee else "") or ""),
    ("Cotização resgate (D+)", 20, lambda f: f.redemption_quotation_days),
    ("Líquidação resgate (D+)", 21, lambda f: f.redemption_payment_days),
    ("Carência (dias)", 15, lambda f: f.grace_days),
    ("Tributação", 16, lambda f: f.taxation or ""),
    ("Fechado p/ captação", 19, lambda f: _yes_no(f.closed_for_investment)),
    ("Fontes", 26, lambda f: ", ".join(SOURCE_LABELS.get(s, s) for s in f.sources)),
    ("Nº de fontes", 12, lambda f: len(f.sources) or None),
    # Uma coluna por fonte: e o que permite filtrar "so no site" ou montar uma
    # tabela dinamica cruzando plataforma x fonte.
    ("Open Finance", 13, lambda f: _yes_no(f.in_source("open-finance"))),
    ("Prateleira (site)", 17, lambda f: _yes_no(
        f.on_listing
    )),
    ("Área logada", 13, lambda f: _yes_no(f.in_source("authenticated"))),
    ("Link", 42, lambda f: f.detail_url or ""),
]

SOURCE_LABELS = {
    "open-finance": "Open Finance",
    "html-scrape": "Site (HTML)",
    "playwright": "Site (navegador)",
    "api-json": "API interna",
    "authenticated": "Área logada",
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


# O que "no mercado" quer dizer nas colunas de captacao (pedido de 19/10/2026):
# a mesma frase na aba Metodologia e no comentario de cada cabecalho.
DEFINICAO_CAPTACAO_NO_MERCADO = (
    "Captação líquida do FUNDO inteiro no período: aplicações menos resgates de "
    "todos os cotistas, por todos os distribuidores e plataformas, somadas do informe "
    "diário da CVM (a classe, com as subclasses). Não é a captação que a casa fez com "
    "o fundo: a CVM não publica fluxo por distribuidor, e um fundo em três vitrines "
    "entra inteiro nas três. O mesmo vale para cotistas e PL no mercado. O período de "
    "cada aba está na aba Metodologia."
)


def _comentar(celula, texto: str) -> None:
    """Um comentario (a nota que aparece ao passar o mouse) num cabecalho."""
    celula.comment = Comment(texto, "Monitor de fundos", height=170, width=360)


def _yes_no(value: bool | None) -> str:
    if value is None:
        return ""
    return "Sim" if value else "Não"


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
        headers.append("Só nesta casa (por CNPJ)")

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
        value=f'=IF({cnpj}="","",IF(COUNTIF({data_range},{cnpj})=1,"Sim","Não"))',
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
    titulo: str = Config.titulo_planilha,
    com_exclusividade: bool = True,
) -> None:
    """Resumo por plataforma x classe, todo em formulas sobre o Consolidado."""
    sheet = workbook.create_sheet("Resumo", 0)
    sheet.column_dimensions["A"].width = 24
    sheet.cell(row=1, column=1, value=titulo).font = _TITLE_FONT
    sheet.cell(
        row=2, column=1,
        value=f"Coleta de {generated_at.strftime('%d/%m/%Y %H:%M')}",
    ).font = _NOTE_FONT

    classes = [ASSET_CLASS_LABELS[c] for c in AssetClass]
    headers = ["Plataforma", *classes, "Total", "Com CNPJ", "Gestoras distintas"]
    if com_exclusividade:
        headers.append("Só nesta vitrine")
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
        if not com_exclusividade:
            pass
        elif listing_last_row > 1:
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
            value="Fontes indisponíveis nesta coleta (não aparecem acima):",
        ).font = _ALERT_FONT
        note_row += 1
        for snapshot in unavailable:
            sheet.cell(
                row=note_row, column=1,
                value=f"  {_platform_label(snapshot.platform)}: {snapshot.error or 'sem detalhe'}",
            ).font = _NOTE_FONT
            note_row += 1
        note_row += 1

    exclusividade = (
        '  "Só nesta vitrine" é fórmula sobre a aba Vitrine varejo, e não sobre o Consolidado: exclusividade',
        "  se mede na vitrine. Contada sobre o catálogo ela somava fundo que a casa administra e ninguém vê.",
        "  Fundos sem CNPJ ficam fora dessa conta, porque comparar por nome entre casas gera falso exclusivo.",
    ) if com_exclusividade else ()
    for line in (
        "Como ler esta planilha:",
        "  As contagens por classe e os totais são fórmulas sobre a aba Consolidado (o catálogo);",
        *exclusividade,
        "  \"Gestoras distintas\" é um valor da coleta, não uma fórmula: contar valores únicos exigiria",
        "  função de matriz, que nem todo leitor de planilha avalia da mesma forma.",
        "  Uma plataforma com poucos fundos pode ser fonte parcial, não prateleira enxuta - confira a aba Coleta.",
    ):
        sheet.cell(row=note_row, column=1, value=line).font = _NOTE_FONT
        note_row += 1


def _add_cvm(workbook: Workbook, cvm: CvmSnapshot) -> None:
    sheet = workbook.create_sheet("Radar CVM")
    headers = [
        "Casa", "Fundo", "CNPJ", "Gestora", "Administrador", "Tipo",
        "Situação", "Pré-operacional", "Data de registro", "Início da situação",
        "Patrimônio líquido (R$)",
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
            _platform_label(fund.house), nome_de_fundo(fund.name), format_cnpj(fund.cnpj),
            fund.manager_name or "", fund.admin_name or "", fund.fund_type or "",
            fund.situation or "", "Sim" if fund.is_pre_operational else "Não",
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
        "Plataforma", "Fonte", "Fundos trazidos", "Só nesta fonte",
        "Total da casa (união)", "% do total",
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
        '"Fundos trazidos" é quanto aquela fonte devolveu sozinha; a soma pode passar do total',
        "da casa, porque o mesmo fundo costuma aparecer em mais de uma fonte.",
        '"Só nesta fonte" é o que se perderia desligando aquela coleta - o número que decide',
        "se manter o scraping de uma casa compensa o tempo que ele custa.",
    ):
        sheet.cell(row=note, column=1, value=line).font = _NOTE_FONT
        note += 1


def _add_collection_health(
    workbook: Workbook, snapshots: list[ShelfSnapshot], cvm: CvmSnapshot | None
) -> None:
    sheet = workbook.create_sheet("Coleta")
    headers = ["Fonte", "Status", "Fontes consultadas", "Fundos", "Observação"]
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


_OWN_LABELS = {True: "Própria", False: "Terceiros", None: "-"}


def _add_listing_sheet(
    workbook: Workbook, snapshots: list[ShelfSnapshot], com_exclusividade: bool = True
) -> int:
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
        ("Aplicação mínima (R$)", 20), ("Líquidação resgate (D+)", 21),
        ("Situação CVM", 26), ("Só nesta vitrine", 16), ("Fontes", 26),
    ]
    if not com_exclusividade:
        headers = [h for h in headers if h[0] != "Só nesta vitrine"]
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
                nome_de_fundo(fund.name),
                format_cnpj(fund.cnpj) if fund.cnpj else "",
                fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM) or "",
                fund.raw_category or "",
                # A gestora mapeada, a mesma que decide o "Produto" ao lado.
                _offer_manager(fund) or "",
                _OWN_LABELS[own],
                fund.admin_fee_pct,
                fund.min_initial,
                fund.redemption_payment_days,
                fund.extra.get(KEY_SITUACAO) or "",
                None,  # formula abaixo
                ", ".join(SOURCE_LABELS.get(s, s) for s in fund.sources),
            ]
            if not com_exclusividade:
                del values[11]
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
        for r in (range(2, row + 1) if com_exclusividade else ()):
            cell = sheet.cell(
                row=r,
                column=12,
                value=(
                    f'=IF(C{r}="","",IF(COUNTIF({cnpj_range},C{r})=1,"Sim","Não"))'
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
    workbook: Workbook,
    snapshots: list[ShelfSnapshot],
    listing_last_row: int,
    retail_flow: dict | None = None,
    grade: dict | None = None,
    familias=None,
    distribuivel=None,
    retornos: dict | None = None,
    prazo_dos_retornos: str = "",
) -> None:
    """Abas "Gaps XP", "Gaps cobertos", "Gaps por categoria" e "Famílias".

    `retornos` (`performance.rentabilidade_por_cnpj`) poe a rentabilidade de
    cada gap em 12 e em 3 meses, com a mediana da categoria, como referencia
    (16/10/2026); `prazo_dos_retornos` vai nas notas com a janela de cada prazo.

    Os numeros vem de build_gap_block - os mesmos do dashboard, digest e deck -
    e a ORDEM vem de `ranquear_gaps`, a mesma das outras tres superficies:
    esforco de fechar primeiro, captacao dentro de cada faixa. Sem isso a
    planilha era a unica a abrir por presenca e PL, e o leitor que comparava
    as duas achava que eram listas diferentes.

    A coluna de verificacao e formula sobre a aba "Vitrine varejo" (intervalo
    limitado as linhas de dado): reage a correcoes manuais na vitrine sem
    reexportar. Os gaps que a XP cobre - por fundo dela, pela lista da casa
    ou em outra faixa de liquidez - vao para "Gaps cobertos", com o motivo e
    a evidencia: saem da contagem, nao da planilha.
    """
    block = build_gap_block(snapshots, familias=familias, distribuivel=distribuivel)
    if block.get("available"):
        block = {**block, "funds": com_rentabilidade(
            ranquear_gaps(block["funds"], retail_flow, grade), retornos or {}
        )}

    sheet = workbook.create_sheet("Gaps XP")
    if not block.get("available"):
        sheet.column_dimensions["A"].width = 110
        sheet.cell(row=1, column=1, value="Gaps de prateleira da XP").font = _TITLE_FONT
        sheet.cell(
            row=3, column=1,
            value=f"Análise suprimida nesta execução: {block.get('reason')}.",
        ).font = _ALERT_FONT
        sheet.cell(
            row=4, column=1,
            value="Sem a vitrine da XP coletada, todo fundo concorrente pareceria "
            "gap - o bloco prefere se calar a inventar número.",
        ).font = _NOTE_FONT
        return

    colunas: list[tuple[str, str, int]] = [
        ("nome", "Fundo", 52), ("cnpj", "CNPJ", 20), ("categoria", "Categoria oficial", 34),
        ("gestora", "Gestora (veículo)", 34), ("estrategia", "Gestora da estratégia", 34),
        ("n_casas", "Nº casas", 10), ("casas", "Casas", 30),
        ("captacao", "Captação no mercado (R$)", 22),
        ("semana", "Captação na semana (R$)", 20),
        ("ritmo", "Ritmo na semana", 16),
        ("cotistas", "Cotistas ganhos no mercado", 24),
        ("rent12", "Rentabilidade 12 meses (%)", 16),
        ("cat12", "Mediana da categoria 12 meses (%)", 18),
        ("rent3", "Rentabilidade 3 meses (%)", 16),
        ("cat3", "Mediana da categoria 3 meses (%)", 18),
        ("esforco", "Esforço de fechar", 26),
        ("veredito", "Veredito", 26), ("familia", "Família (master)", 44),
        ("dmais", "Resgate (D+)", 12),
    ]
    colunas += [
        ("minimo", "Menor mínimo (R$)", 18), ("taxa", "Menor taxa adm (%)", 17),
        ("pl", "PL CVM (R$)", 18), ("gestora_xp", "Gestora na base XP", 18),
        ("equivalente", "Possível equivalente XP (baixa confiança)", 44),
        ("verificacao", "Verificação vs. Vitrine varejo", 26),
    ]
    coluna = {chave: indice for indice, (chave, _, _) in enumerate(colunas, start=1)}
    for index, (_, name, width) in enumerate(colunas, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(colunas))
    _comentar(sheet.cell(row=1, column=coluna["captacao"]), DEFINICAO_CAPTACAO_NO_MERCADO)

    baseline_label = block["baseline_label"].replace('"', "")
    row = 1
    for fund in block["funds"]:
        row += 1
        veredito = fund.get("veredito") or {}
        equivalente = (
            nome_de_fundo(
                veredito.get("nome_possivel_equivalente")
                or fund.get("possible_xp_equivalent") or ""
            )
        )
        valores = {
            "nome": nome_de_fundo(fund["name"]),
            "cnpj": fund["cnpj_display"],
            "categoria": fund["category"],
            "gestora": fund["manager"] or "",
            "estrategia": fund.get("gestora_estrategia") or fund["manager"] or "",
            "n_casas": fund["houses_count"],
            "casas": fund["houses_label"],
            "captacao": fund.get("captacao_liquida"),
            "semana": fund.get("captacao_semana"),
            "ritmo": nome_da_tendencia(fund.get("ritmo_semanal")),
            "cotistas": fund.get("variacao_cotistas"),
            # Pontos percentuais, como a taxa de adm: _PCT so concatena o "%".
            "rent12": fund.get("retorno_12m"),
            "cat12": fund.get("mediana_12m"),
            "rent3": fund.get("retorno_3m"),
            "cat3": fund.get("mediana_3m"),
            "esforco": fund.get("esforco") or "",
            "veredito": veredito.get("rotulo") or "não verificado (sem carteira)",
            "familia": nome_de_fundo((fund.get("familia") or {}).get("nome")),
            "dmais": fund.get("dmais"),
            "minimo": fund["min_initial"],
            "taxa": fund["admin_fee_pct"],
            "pl": fund["pl_cvm"],
            "gestora_xp": _yes_no(fund["manager_on_xp"]),
            "equivalente": equivalente,
            "verificacao": None,  # formula abaixo
        }
        for chave, index in coluna.items():
            cell = sheet.cell(row=row, column=index, value=valores[chave])
            cell.font = _BODY_FONT
            cell.border = _BORDER
        for chave, formato in (
            ("n_casas", _INT), ("captacao", _MONEY), ("semana", _MONEY), ("cotistas", _INT),
            ("rent12", _PCT_RETORNO), ("cat12", _PCT_RETORNO), ("rent3", _PCT_RETORNO),
            ("cat3", _PCT_RETORNO),
            ("dmais", _INT), ("minimo", _MONEY), ("taxa", _PCT), ("pl", _MONEY),
        ):
            sheet.cell(row=row, column=coluna[chave]).number_format = formato
        if listing_last_row > 1:
            plat_range = f"'Vitrine varejo'!$A$2:$A${listing_last_row}"
            cnpj_range = f"'Vitrine varejo'!$C$2:$C${listing_last_row}"
            letra = get_column_letter(coluna["cnpj"])
            celula = sheet.cell(
                row=row, column=coluna["verificacao"],
                value=(
                    f'=IF(${letra}{row}="","",IF(COUNTIFS({plat_range},'
                    f'"{baseline_label}",{cnpj_range},${letra}{row})=0,'
                    f'"Confirma gap","{baseline_label} vitrina - revisar"))'
                ),
            )
            celula.font = _BODY_FONT
            celula.border = _BORDER

    if row > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{row}"
    else:
        sheet.cell(
            row=2, column=1,
            value="Nenhum gap a fechar: toda vitrine concorrente coletada está "
            "na base da XP ou coberta - por fundo da XP, pela lista da casa ou em "
            "outra faixa de liquidez (aba 'Gaps cobertos').",
        ).font = _NOTE_FONT
    note_row = row + 2
    funil = frase_do_funil(block)
    if funil:
        sheet.cell(row=note_row, column=1, value=funil).font = _BODY_FONT
        note_row += 1
    sheet.cell(
        row=note_row, column=1,
        value="Gap aparente = fundo de varejo na vitrine de 1+ concorrente e fora da "
        "base da XP (por CNPJ). Comparação vitrine x vitrine: o catálogo Open "
        "Finance não conta em nenhum dos lados. Gap real = gap aparente que a XP "
        "não tem de forma nenhuma: nem espelho, nem equivalente, nem correlato da "
        "mesma gestora, nem na lista da casa, nem em outra faixa de liquidez (desde "
        "19/10/2026); os cobertos estão na aba 'Gaps cobertos', com o motivo e a "
        "evidência. 'Gestora na base XP': a gestora da estratégia tem algum fundo "
        "na vitrine da XP ou na lista da casa (19/10/2026). Ela e o 'Possível "
        "equivalente' informam - nunca removem um fundo da lista.",
    ).font = _NOTE_FONT
    for nota in notas_da_cobertura(block):
        note_row += 1
        sheet.cell(row=note_row, column=1, value=nota).font = (
            _ALERT_FONT if nota.startswith("Atenção") else _NOTE_FONT
        )
    if prazo_dos_retornos:
        note_row += 1
        sheet.cell(row=note_row, column=1, value=prazo_dos_retornos).font = _NOTE_FONT
    if block["baseline_warning"]:
        note_row += 1
        sheet.cell(
            row=note_row, column=1, value=f"Atenção: {block['baseline_warning']}."
        ).font = _ALERT_FONT
    if block["excluded"]:
        note_row += 1
        out = "; ".join(f"{e['label']} ({e['reason']})" for e in block["excluded"])
        sheet.cell(
            row=note_row, column=1, value=f"Fora do universo: {out}."
        ).font = _NOTE_FONT
    invalidas = (block.get("distribuivel") or {}).get("invalidas") or []
    for invalida in invalidas[:50]:
        note_row += 1
        sheet.cell(
            row=note_row, column=1,
            value=(
                f"Lista distribuível, linha recusada: {invalida['arquivo']}, linha "
                f"{invalida['linha']} - {invalida['motivo']}."
            ),
        ).font = _NOTE_FONT
    sheet.freeze_panes = "A2"

    _add_gaps_cobertos_sheet(workbook, block)

    cat_sheet = workbook.create_sheet("Gaps por categoria")
    competitors = block["competitors"]
    cat_headers = (
        [("Categoria", 34), (f"{block['baseline_label']} (vitrine)", 20)]
        + [(c["label"], 16) for c in competitors]
        + [("Melhor concorrente", 24), ("Gaps reais", 12), ("Cobertos", 12)]
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
                entry.get("cobertos", 0),
            ]
        )
        for index, value in enumerate(values, start=1):
            cell = cat_sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
    if row == 1:
        cat_sheet.cell(
            row=2, column=1, value="Nenhum gap por categoria nesta execução."
        ).font = _NOTE_FONT
    cat_sheet.cell(
        row=row + 2, column=1,
        value="Contagens por vitrine (varejo) de cada casa na categoria; "
        "'Gaps reais' = quantos gaps da aba 'Gaps XP' caem na categoria; "
        "'Cobertos' = gaps aparentes da categoria que a XP cobre por fundo dela (espelho, "
        "equivalente ou correlato), pela lista da casa ou em outra faixa de liquidez "
        "(aba 'Gaps cobertos'). Categorias ordenadas por gaps reais; "
        "além das primeiras, agregadas em 'Outras'.",
    ).font = _NOTE_FONT
    cat_sheet.freeze_panes = "A2"

    if familias is not None:
        _add_familias_sheet(workbook, block, familias, snapshots, distribuivel)


def _add_gaps_cobertos_sheet(workbook: Workbook, block: dict) -> None:
    """Os gaps aparentes que a XP ja cobre, com o motivo e a prova de cada um.

    Desde 19/10/2026 sao tres motivos (`aggregate._cobertura`): a carteira
    (espelho, equivalente, correlato), a lista da casa e a outra faixa de
    liquidez. A aba Gaps XP fica so com o gap efetivo.
    """
    sheet = workbook.create_sheet("Gaps cobertos")
    colunas = [
        ("Coberto por", 24),
        ("Fundo concorrente", 50), ("CNPJ", 20), ("Casas", 26), ("Categoria oficial", 30),
        ("Veredito", 24), ("Confiança", 11), ("Fundo da XP que cobre", 50),
        ("CNPJ que cobre", 20), ("Ligação", 14), ("Master", 50),
        ("Resgate do gap (D+)", 12), ("Resgate da XP (D+)", 12),
        ("Distância de carteira", 12), ("Ativos em comum", 12),
        ("Carteira (competência)", 14), ("Na lista distribuível", 20),
        ("Esforço", 22),
    ]
    for index, (name, width) in enumerate(colunas, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(colunas))
    row = 1
    # O esforco so vale para o que a XP ainda tem de mexer - expor, ativar ou a
    # versao de liquidez; o coberto por fundo da XP nao tem.
    esforcos = {
        f["cnpj"]: f.get("esforco")
        for f in ranquear_gaps([
            c for c in block.get("cobertos") or [] if c.get("cobertura") != COBERTURA_CARTEIRA
        ])
    }
    for fund in block.get("cobertos") or []:
        # A prova do motivo: o veredito da carteira, ou o da lista da casa.
        v = fund.get("prova") or fund.get("veredito") or {}
        row += 1
        valores = [
            fund.get("cobertura_rotulo") or "",
            nome_de_fundo(fund["name"]), fund["cnpj_display"], fund["houses_label"],
            fund["category"],
            v.get("rotulo") or "", v.get("confianca") or "",
            # Na lista com o proprio CNPJ, quem cobre e o proprio fundo.
            nome_de_fundo(
                v.get("nome_cobre") or (fund["name"] if v.get("cobre") == fund["cnpj"] else "")
            ),
            format_cnpj(v.get("cobre")) if v.get("cobre") else "",
            v.get("ligacao") or "", nome_de_fundo(v.get("nome_master")),
            v.get("dmais"), v.get("dmais_cobre"),
            v.get("distancia"), v.get("sobreposicao"),
            competencia_br(v.get("competencia")) if v.get("competencia") else "-",
            ROTULOS_SITUACAO.get(fund.get("situacao_distribuivel"), "-"),
            esforcos.get(fund["cnpj"]) or "",
        ]
        for index, value in enumerate(valores, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        for index in (12, 13):
            sheet.cell(row=row, column=index).number_format = _INT
        for index in (14, 15):
            sheet.cell(row=row, column=index).number_format = "0%"
    if row > 1:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{row}"
    else:
        sheet.cell(
            row=2, column=1,
            value=(
                "Nenhum gap aparente coberto pela carteira, pela lista da casa ou em outra "
                "faixa de liquidez."
                if (block.get("carteira") or {}).get("available") else
                "Sem a carteira mensal da CVM nesta execução: espelho e equivalência "
                "não foram verificados."
            ),
        ).font = _NOTE_FONT
    sheet.cell(
        row=row + 2, column=1,
        value="Coberto por: fundo da XP (mesma classe, espelho, equivalente ou correlato, "
        "pela carteira da CVM), lista da casa (a XP já distribui ou pode distribuir) ou "
        "outra faixa de liquidez (a XP tem a estratégia em outro D+). Nenhum deles é gap "
        "real: a aba Gaps XP fica só com os reais (19/10/2026). Esforço: o que falta "
        "para o cliente ver o fundo - expor na vitrine, ativar a distribuição ou a "
        "versão de liquidez. "
        "Mesma classe: o concorrente publica outro CNPJ da mesma classe. "
        "Espelho: o gap e o fundo da XP chegam ao mesmo master pela carteira "
        "(95% ou mais do PL em cotas de um único fundo). Equivalente: master da "
        "mesma gestora e categoria, com carteira próxima e ativos em comum. "
        "Distância = fração da carteira em faixas diferentes; ativos em comum = "
        "fração da carteira nos mesmos emissores, ações ou fundos.",
    ).font = _NOTE_FONT
    sheet.freeze_panes = "A2"


def _add_familias_sheet(
    workbook: Workbook, block: dict, familias, snapshots: list[ShelfSnapshot],
    distribuivel,
) -> None:
    """As familias das gestoras dos gaps aparentes, uma linha por classe.

    So entram familias com alguma classe numa vitrine coletada ou na lista
    distribuivel: a gestora grande tem milhares de fundos exclusivos que
    nenhuma prateleira mostra, e listar todos afogaria as que importam.
    """
    from shelf_monitor.report.familias import presenca_nas_vitrines

    presenca = presenca_nas_vitrines(familias, snapshots)
    baseline_id = block.get("baseline_id") or "xp"
    raizes = {
        (f.get("veredito") or {}).get("raiz_gestora")
        for f in (block.get("funds") or []) + (block.get("cobertos") or [])
    } - {None}
    vistas: set[str] = set()
    linhas: list[list] = []
    for raiz in sorted(raizes):
        for familia in familias.familias_da_gestora(raiz):
            if familia.id in vistas:
                continue
            visiveis = [
                c for c in familia.cnpjs
                if presenca.get(c) or (distribuivel and distribuivel.situacao_de(c))
            ]
            if not visiveis:
                continue
            vistas.add(familia.id)
            for numero, versao in enumerate(familia.versoes, start=1):
                for membro in versao.membros:
                    casas = ordem_canonica(presenca.get(membro.cnpj, set()))
                    situacao = distribuivel.situacao_de(membro.cnpj) if distribuivel else None
                    linhas.append([
                        familia.gestora or "", nome_de_fundo(familia.nome), familia.anbima or "",
                        familia.resumo_perfil(), familia.pl or None,
                        f"v{numero}", membro.papel, nome_de_fundo(membro.nome),
                        format_cnpj(membro.cnpj), membro.dmais,
                        membro.faixa_liquidez or "-", membro.pl,
                        ", ".join(_platform_label(c) for c in casas) or "-",
                        "Sim" if baseline_id in casas else "Não",
                        ROTULOS_SITUACAO.get(situacao, "-"),
                    ])
    sheet = workbook.create_sheet("Famílias")
    colunas = [
        ("Gestora", 34), ("Família (master principal)", 48), ("Categoria ANBIMA", 30),
        ("Carteira da família", 44), ("PL da família (R$)", 18), ("Versão", 8),
        ("Papel", 10), ("Fundo", 50), ("CNPJ", 20), ("Resgate (D+)", 11),
        ("Faixa de liquidez", 16), ("PL (R$)", 16), ("Vitrines", 26),
        (f"Na vitrine {block.get('baseline_label') or 'XP'}", 12), ("Lista distribuível", 18),
    ]
    for index, (name, width) in enumerate(colunas, start=1):
        sheet.cell(row=1, column=index, value=name)
        sheet.column_dimensions[get_column_letter(index)].width = width
    _style_header(sheet, 1, len(colunas))
    for row, valores in enumerate(linhas, start=2):
        for index, value in enumerate(valores, start=1):
            cell = sheet.cell(row=row, column=index, value=value)
            cell.font = _BODY_FONT
            cell.border = _BORDER
        sheet.cell(row=row, column=5).number_format = _MONEY
        sheet.cell(row=row, column=10).number_format = _INT
        sheet.cell(row=row, column=12).number_format = _MONEY
    ultima = len(linhas) + 1
    if linhas:
        sheet.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{ultima}"
    sheet.cell(
        row=ultima + 2, column=1,
        value="Famílias de estratégia das gestoras que aparecem nos gaps, com ao "
        "menos uma classe numa vitrine coletada ou na lista distribuível. Versão = "
        "um master e os espelhos que investem nele; versões diferentes da mesma "
        "família são classes paralelas com carteira equivalente. Para a gestora "
        "inteira: python -m shelf_monitor familias seguido do nome da gestora.",
    ).font = _NOTE_FONT
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
                label, nome_de_fundo(fund.name),
                format_cnpj(fund.cnpj) if fund.cnpj else "",
                reason,
                fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM) or "",
                _offer_manager(fund) or "",
                "Sim" if fund.on_listing else "Não",
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
                value=(
                    f"nova linha de base - {diff.suspect_reason}"
                    if diff.nova_linha_de_base
                    else "primeira coleta - sem base de comparação"
                ),
            ).font = _NOTE_FONT
            continue
        if diff.suspect:
            row += 1
            sheet.cell(row=row, column=1, value=label).font = _BODY_FONT
            sheet.cell(row=row, column=2, value="SUSPEITO").font = _ALERT_FONT
            sheet.cell(
                row=row, column=6,
                value=(
                    "coleta parcial ou queda brusca - saídas não listadas para "
                    "não virar churn falso"
                ),
            ).font = _ALERT_FONT

        entries: list[tuple[str, str, str, str, str]] = []
        from shelf_monitor.profile import restriction_reason

        def is_retail(fund) -> bool:
            return restriction_reason(fund) is None

        for fund in filter(is_retail, diff.added):
            entries.append((
                "entrada", nome_de_fundo(fund.name),
                format_cnpj(fund.cnpj) if fund.cnpj else "",
                "Sim" if fund.on_listing else "Não", "",
            ))
        if not diff.suspect:
            for fund in filter(is_retail, diff.removed):
                entries.append((
                    "saída", nome_de_fundo(fund.name),
                    format_cnpj(fund.cnpj) if fund.cnpj else "",
                    "Sim" if fund.on_listing else "Não", "",
                ))
        for change in diff.changed:
            detail = "; ".join(
                f"{c.field}: {c.old} -> {c.new}" for c in change.changes
            )
            cnpj = change.identity if change.identity.isdigit() else ""
            entries.append((
                "alteração", change.name,
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
    suprimidos: dict[str, str] | None = None,
    performance_12m: dict | None = None,
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
        detalhe = periodo_br(periodo.get("de"), periodo.get("ate"))
        if competencias:
            detalhe += (
                f" ({contagem(len(competencias), 'competência', 'competências')}: "
                f"{', '.join(competencia_br(m) for m in competencias)})"
            )
        ausentes = periodo.get("meses_ausentes") or []
        if ausentes:
            detalhe += f" - ausentes: {', '.join(competencia_br(m) for m in ausentes)}"
        abas = [
            "Captação por casa", "Captação por fundo", "Tendência de captação",
            "Oportunidades de grade",
        ]
        if (suprimidos or {}).get(BLOCO_CNPJ):
            abas.remove("Oportunidades de grade")
        for aba in abas:
            linhas.append((aba, "Informe diário da CVM (INF_DIARIO)", detalhe))

    blocos_rent = blocos_de_rentabilidade(performance_12m, performance)
    for posicao, bloco in enumerate(blocos_rent):
        janela = janela_do_bloco(bloco)
        detalhe = periodo_br(janela.get("de"), janela.get("ate"))
        if janela.get("pregoes"):
            detalhe += f" ({milhar(janela['pregoes'])} pregões)"
        abas = [f"Rentabilidade {janela['rotulo']}"]
        # "Onde esta a gente" lista os fundos da janela mais longa.
        if posicao == 0 and not (suprimidos or {}).get(BLOCO_COTISTAS):
            abas.append("Onde está a gente")
        for aba in abas:
            linhas.append((
                aba,
                f"Cota patrimonial do INF_DIARIO, base na {janela['base']}",
                detalhe,
            ))

    if rentabilidade and rentabilidade.get("available"):
        janelas = "; ".join(
            f"{j['rotulo']}: {competencia_br(j['de'])} a {competencia_br(j['ate'])}"
            for j in rentabilidade.get("janelas") or []
        )
        linhas.append((
            "Rentabilidade por fundo",
            "Cota patrimonial do INF_DIARIO, base no FECHAMENTO DA COMPETÊNCIA "
            "ANTERIOR (convenção de lâmina)",
            janelas,
        ))

    if custo and custo.get("available"):
        idade = (custo.get("universo") or {}).get("idade_maxima_meses")
        linhas.append((
            "Custo e acesso",
            "Extrato de informações da CVM (EXTRATO)",
            f"Não é intervalo: cada classe tem a data-base da própria entrega, "
            f"e só entram as de até {idade} meses",
        ))
        linhas.append((
            "Custo por classe",
            "Extrato de informações da CVM (EXTRATO)",
            f"O mesmo de Custo e acesso: a data-base da entrega de cada classe, "
            f"até {idade} meses",
        ))

    if mandato and mandato.get("available"):
        linhas.append((
            "Mandato declarado",
            "Extrato de informações da CVM (EXTRATO)",
            "Não se aplica: condição vigente do regulamento, não medida de período",
        ))

    return linhas


def _add_methodology_sheet(
    workbook: Workbook,
    snapshots: list[ShelfSnapshot],
    enrichment: EnrichmentStats | None,
    generated_at: datetime,
    periodos: list[tuple[str, str, str]] | None = None,
    suprimidos: dict[str, str] | None = None,
    fontes: list[Fonte] | None = None,
) -> None:
    """Aba "Metodologia": proveniencia e cobertura - o que um comite pergunta."""
    suprimidos = suprimidos or {}
    sheet = workbook.create_sheet("Metodologia")
    sheet.column_dimensions["A"].width = 34
    sheet.column_dimensions["B"].width = 90

    def line(row: int, key: str, value: str, font=None) -> int:
        sheet.cell(row=row, column=1, value=key).font = _HEADER_FONT if font is None else font
        sheet.cell(row=row, column=1).fill = _HEADER_FILL if font is None else PatternFill()
        sheet.cell(row=row, column=2, value=value).font = _BODY_FONT
        return row + 1

    row = 1
    sheet.cell(row=row, column=1, value="Metodologia e proveniência").font = _TITLE_FONT
    row += 2

    row = line(row, "Gerado em", generated_at.strftime("%d/%m/%Y %H:%M"))
    if fontes:
        row += 1
        sheet.cell(row=row, column=1, value="Fontes").font = _TITLE_FONT
        row += 1
        for col, titulo in enumerate(
            ("Fonte", "O que entra", "Endereço para consulta", "Arquivos que o monitor lê"),
            start=1,
        ):
            cell = sheet.cell(row=row, column=col, value=titulo)
            cell.font = _HEADER_FONT
            cell.fill = _HEADER_FILL
        row += 1
        for fonte in fontes:
            _link(sheet.cell(row=row, column=1), fonte.url, fonte.nome)
            sheet.cell(row=row, column=2, value=fonte.uso).font = _BODY_FONT
            _link(sheet.cell(row=row, column=3), fonte.url, fonte.url)
            if fonte.url_dados:
                _link(sheet.cell(row=row, column=4), fonte.url_dados, fonte.url_dados)
            row += 1
        sheet.column_dimensions["C"].width = 60
        sheet.column_dimensions["D"].width = 60
    if periodos:
        row += 1
        sheet.cell(row=row, column=1, value="Período de cada aba").font = _TITLE_FONT
        row += 1
        sheet.cell(row=row, column=1, value="Aba").font = _HEADER_FONT
        sheet.cell(row=row, column=1).fill = _HEADER_FILL
        sheet.cell(row=row, column=2, value="Fonte e período").font = _HEADER_FONT
        sheet.cell(row=row, column=2).fill = _HEADER_FILL
        row += 1
        for aba, fonte, detalhe in periodos:
            sheet.cell(row=row, column=1, value=aba).font = _BODY_FONT
            sheet.cell(row=row, column=2, value=f"{fonte} - {detalhe}").font = _BODY_FONT
            row += 1
        row += 1
    row = line(
        row, "Vitrine (definição)",
        "Fundo presente no canal da própria casa: site público, API do portal ou "
        "área logada. Open Finance NÃO conta como vitrine - é o catálogo "
        "regulatório (tudo que a casa administra), usado aqui para enriquecer "
        "campos e medir o catálogo.",
    )
    row = line(
        row, "Perfil de varejo",
        "Fundos exclusivos/restritos (registro CVM, público profissional, flag "
        "da casa ou nome) ficam FORA das abas de análise e moram na aba "
        "'Restritos (auxiliar)' com o motivo. Qualificado não é motivo de corte.",
    )
    row = line(row, "Captação no mercado", DEFINICAO_CAPTACAO_NO_MERCADO)
    if suprimidos.get(BLOCO_CNPJ):
        row = line(
            row, "Gaps e exclusivos por CNPJ",
            f"Não se aplica a este monitor: {suprimidos[BLOCO_CNPJ]}. Por isso "
            "não há abas de gaps nem de oportunidade de grade, e nenhuma coluna "
            "'Só nesta vitrine' ou 'Exclusivo'.",
        )
    else:
        row = line(
            row, "Gaps XP",
            "Gap aparente = fundo de varejo na vitrine de 1+ concorrente e fora da "
            "vitrine da XP, por CNPJ. Vitrine x vitrine: o catálogo Open Finance "
            "não conta em nenhum dos lados. Com a carteira mensal da CVM, cada gap "
            "aparente recebe um veredito: coberto por espelho (chega ao mesmo "
            "master que um fundo da XP), coberto por equivalente (mesma gestora e "
            "categoria, carteira próxima e ativos em comum), coberto por correlato "
            "(mesma gestora e mesmo nome de estratégia, em outra versão: Advisory, "
            "Seleção, Access, II, Plus), coberto pela lista da casa (a XP já "
            "distribui ou pode distribuir), coberto em outra faixa de liquidez, ou "
            "gap real (de estratégia, de gestora, ou sem carteira que decida). Só o "
            "gap real fica na aba 'Gaps XP' (19/10/2026); os cobertos saem da "
            "contagem e vão para a aba 'Gaps cobertos' com o motivo e a evidência. "
            "XP indisponível ou sem vitrine "
            "suprime a análise (salvo com lista distribuível, que vira a base); XP "
            "parcial gera aviso; concorrente indisponível ou sem vitrine sai do "
            "universo com motivo. 'Gestora na base XP' = a gestora tem algum fundo "
            "na vitrine da XP ou na lista da casa; ela e o 'Possível equivalente' "
            "informam - nunca removem um fundo da lista.",
        )
        row = line(
            row, "Lista distribuível",
            "CSVs soltos em data/distribuivel/entrada/ (modelo em "
            "data/distribuivel/modelo.csv): os fundos que a XP distribui ou pode "
            "distribuir. Um gap da vitrine que a lista cobre vira 'expor na "
            "vitrine' (ou 'ativar distribuição'), o degrau mais barato da escada "
            "de esforço. Linha que não se resolve no cadastro da CVM é listada, "
            "com arquivo e motivo, no fim da aba 'Gaps XP'.",
        )
    if suprimidos.get(BLOCO_COTISTAS):
        row = line(
            row, "Cotistas",
            f"Não se aplica a este monitor: {suprimidos[BLOCO_COTISTAS]}. Por isso "
            "não há aba 'Onde está a gente' nem colunas de cotistas.",
        )
    row = line(
        row, "Precedência de fontes",
        "Open Finance > área logada > API do portal > navegador > HTML. Quando "
        "duas fontes discordam de um campo, vence a de maior confiança; as "
        "demais preenchem lacunas.",
    )
    if enrichment is not None:
        identifiable = max(enrichment.with_cnpj, enrichment.with_cvm_code)
        pct = (
            f"{100 * enrichment.matched / identifiable:.0f}%" if identifiable else "n/a"
        )
        row = line(
            row, "Normalização ANBIMA",
            f"Join com a base oficial CVM pelo CNPJ da classe, pelo CNPJ do fundo "
            f"e pelo código CVM da subclasse ({milhar(enrichment.matched)} de "
            f"{milhar(identifiable)} fundos identificáveis casaram - {pct}; "
            f"{milhar(enrichment.matched_by_cvm_code)} deles só pelo código). Classe "
            "ANBIMA, gestor e situação vêm do registro oficial; a classificação "
            "publicada pela casa, quando diverge, fica na coluna "
            "classificacao_anbima_casa.",
        )
        if enrichment.classified_by_name:
            vias = ", ".join(
                f"{via} {milhar(n)}"
                for via, n in sorted(
                    enrichment.by_name_via.items(), key=lambda kv: -kv[1]
                )
            )
            row = line(
                row, "Classificação por nome",
                f"{milhar(enrichment.classified_by_name)} fundos que o join por "
                "identidade não alcançou receberam a categoria oficial pelo "
                "nome, casando contra o cadastro da CVM por prefixo de slug "
                "mais taxa, aplicação mínima e prazo de resgate. É o que "
                "coloca na comparação a casa que não publica CNPJ - a vitrine "
                "do Itaú traz CNPJ em 6% dos fundos. Nenhuma dessas vias "
                "preenche CNPJ: elas resolvem a CATEGORIA, não a identidade, e "
                "a via de cada fundo fica na coluna categoria_via. "
                f"Vias usadas: {vias}.",
            )
        row = line(
            row, "Gestora",
            f"Vem do registro da CVM sempre que o fundo casa com a base "
            f"({milhar(enrichment.managers_from_cvm)} fundos nesta geração) - é o que "
            "faz a mesma gestora ter um nome só nas cinco casas. O nome "
            "publicado pela casa, quando diverge, fica em gestor_casa. Sem "
            f"match, {enrichment.managers_inferred} gestoras foram inferidas "
            "pelo prefixo do nome do fundo e vão marcadas como inferidas.",
        )
    else:
        row = line(
            row, "Normalização ANBIMA",
            "NÃO aplicada nesta geração: base de mercado ausente. Rode "
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
                + ", ".join(f"{k}={milhar(v)}" for k, v in snapshot.source_counts.items())
            )
        listing = sum(1 for f in snapshot.funds if f.on_listing)
        parts.append(f"vitrine={milhar(listing)} de {milhar(len(snapshot.funds))}")
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
    performance_12m: dict | None = None,
    custo: dict | None = None,
    mandato: dict | None = None,
    rentabilidade: dict | None = None,
    suprimidos: dict[str, str] | None = None,
    familias=None,
    distribuivel=None,
    captacao_xp: dict | None = None,
) -> Path:
    """Gera a planilha das prateleiras. Devolve o caminho do arquivo.

    `suprimidos` e o `RunReport.suprimidos`: as abas e colunas que contam
    cotistas ou cruzam vitrines por CNPJ saem, e o motivo vai na Metodologia.
    `familias` e `distribuivel` sao os do RunReport: o mapa de familias e a
    lista distribuivel, que viram veredito de cobertura nas abas de gaps.
    `captacao_xp` e o `RunReport.captacao_xp`: a captacao de cada fundo na XP,
    na aba Captacao por fundo.
    """
    generated_at = generated_at or datetime.now()
    usable = [s for s in snapshots if s.funds]
    suprimidos = suprimidos or {}
    sem_cnpj = suprimidos.get(BLOCO_CNPJ)
    sem_cotistas = suprimidos.get(BLOCO_COTISTAS)

    workbook = Workbook()
    last_row = _add_consolidated(workbook, usable)
    listing_last_row = _add_listing_sheet(
        workbook, usable, com_exclusividade=not sem_cnpj
    )
    # O retorno de cada fundo, como referencia nas abas de gaps e de captacao
    # (16/10/2026): 12 e 3 meses, com a mediana da categoria. As abas de
    # captacao e de grade so tem as colunas de 12 meses - a nota delas tambem.
    retornos = rentabilidade_por_cnpj(performance_12m, performance)
    prazo_dos_retornos = prazo_das_colunas_de_rentabilidade(performance_12m, performance)
    prazo_dos_12m = prazo_das_colunas_de_rentabilidade(performance_12m)
    # Gaps recebem os snapshots completos: o proprio bloco decide quem entra
    # no universo e lista os excluidos com motivo.
    if not sem_cnpj:
        _add_gaps_sheets(
            workbook, snapshots, listing_last_row, retail_flow, grade,
            familias=familias, distribuivel=distribuivel,
            retornos=retornos, prazo_dos_retornos=prazo_dos_retornos,
        )
    _add_restricted_sheet(workbook, usable)
    if diffs:
        _add_movements_sheet(workbook, diffs)
    _add_platform_sheets(workbook, usable)
    if cvm and cvm.funds:
        _add_cvm(workbook, cvm)
    _add_source_coverage(workbook, usable)
    if retail_flow and retail_flow.get("available"):
        omitir = set()
        if sem_cnpj:
            omitir |= _FLOW_POR_CNPJ
        if sem_cotistas:
            omitir |= _FLOW_POR_COTISTAS
        if suprimidos.get(BLOCO_PRODUTO_PROPRIO):
            omitir |= _FLOW_POR_PRODUTO
        _add_captacao_casas_sheet(workbook, retail_flow, suprimidos)
        _add_flow_sheet(
            workbook, retail_flow, omitir=frozenset(omitir),
            retornos=retornos, prazo_dos_retornos=prazo_dos_12m,
            captacao_xp=captacao_xp,
        )
        _add_trend_sheet(workbook, retail_flow)
    # A grade e "fundo que capta fora da vitrine da XP", por CNPJ.
    if grade and grade.get("available") and not sem_cnpj:
        _add_grade_sheet(
            workbook, grade, retornos=retornos, prazo_dos_retornos=prazo_dos_12m
        )
    if rentabilidade and rentabilidade.get("available"):
        _add_rentabilidade_sheet(workbook, rentabilidade)
    # Uma aba por janela, a mais longa primeiro. "Onde esta a gente" lista os
    # fundos da mais longa, com o retorno da curta ao lado.
    blocos_rent = blocos_de_rentabilidade(performance_12m, performance)
    for bloco in blocos_rent:
        _add_performance_sheet(workbook, bloco, sem_cotistas=sem_cotistas)
    if blocos_rent and not sem_cotistas:
        _add_gente_sheet(
            workbook, blocos_rent[0], blocos_rent[1] if len(blocos_rent) > 1 else None
        )
    if custo and custo.get("available"):
        _add_custo_sheet(workbook, custo)
        _add_custo_por_classe_sheet(workbook, custo)
    if mandato and mandato.get("available"):
        _add_mandato_sheet(workbook, mandato)
    _add_collection_health(workbook, snapshots, cvm)
    # As fontes saem da mesma funcao das outras superficies; a planilha recebe
    # os blocos soltos, entao monta um relatorio so para a pergunta.
    usadas = fontes_do_relatorio(
        RunReport(
            generated_at=generated_at, snapshots=snapshots, cvm_snapshot=cvm,
            enrichment=enrichment.to_dict() if enrichment else None,
            retail_flow=retail_flow, performance=performance,
            performance_12m=performance_12m,
            rentabilidade=rentabilidade, custo=custo, mandato=mandato,
        ),
        config,
    )
    _add_methodology_sheet(
        workbook, snapshots, enrichment, generated_at,
        periodos=_periodos_das_abas(
            retail_flow, performance, rentabilidade, custo, mandato, suprimidos,
            performance_12m=performance_12m,
        ),
        suprimidos=suprimidos,
        fontes=usadas,
    )
    _add_summary(
        workbook, usable, snapshots, last_row, generated_at, listing_last_row,
        titulo=config.titulo_planilha, com_exclusividade=not sem_cnpj,
    )

    config.reports_dir.mkdir(parents=True, exist_ok=True)
    path = path or (
        config.reports_dir
        / f"{config.prefixo_arquivo}-{generated_at.date().isoformat()}.xlsx"
    )
    return _salvar(workbook, path)


def _link(cell, url: str, texto: str) -> None:
    """Celula com hiperlink para a fonte - o texto e o que a pessoa ve."""
    cell.value = texto
    cell.hyperlink = url
    cell.font = Font(name=FONT, size=10, color="1A5FB4", underline="single")


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
    "Fundo", "CNPJ", "Classe", "Gestora", "Vitrines", "Produto próprio de", "Exclusivo",
    "Captação bruta (R$)", "Resgates (R$)", "Captação líquida (R$)",
    "Captação na semana (R$)",
    "Captação líquida na XP (R$)", "Captação na semana na XP (R$)",
    "Cruzamento com a XP", "Fundo da XP no cruzamento",
    "PL início (R$)", "PL fim (R$)", "Captação líquida / PL início (%)",
    "Cotistas início", "Cotistas fim", "Variação cotistas",
    "Entrada por cotista ganho (R$)",
    "Rentabilidade no período, base no 1o pregão (%)",
    "Rentabilidade 12 meses (%)", "Mediana da categoria 12 meses (%)",
    "Pregões observados", "Tendência", "Ritmo na semana", "Movimento estrutural",
]

# Formato por cabecalho, e nao por posicao: um modulo pode omitir colunas
# (previdencia tira as de cotistas e a de exclusividade) sem que o formato
# de uma escorregue para a vizinha.
_FLOW_MONEY = {
    "Captação bruta (R$)", "Resgates (R$)", "Captação líquida (R$)",
    "Captação na semana (R$)",
    "Captação líquida na XP (R$)", "Captação na semana na XP (R$)",
    "PL início (R$)", "PL fim (R$)", "Entrada por cotista ganho (R$)",
}
_FLOW_PCT = {
    "Captação líquida / PL início (%)",
    "Rentabilidade no período, base no 1o pregão (%)",
    "Rentabilidade 12 meses (%)", "Mediana da categoria 12 meses (%)",
}
_FLOW_INT = {"Cotistas início", "Cotistas fim", "Variação cotistas", "Pregões observados"}
_FLOW_LIQUIDA = "Captação líquida (R$)"
# O que cada supressao tira desta aba.
_FLOW_POR_CNPJ = frozenset({"Exclusivo"})
_FLOW_POR_PRODUTO = frozenset({"Produto próprio de"})
_FLOW_POR_COTISTAS = frozenset({
    "Cotistas início", "Cotistas fim", "Variação cotistas",
    "Entrada por cotista ganho (R$)",
})
# A captacao na XP (20/10/2026, `report.captacao_na_xp`): sai sem as bases da
# XP e nas abas cujas linhas nao passam pelo cruzamento.
_FLOW_NA_XP = frozenset({
    "Captação líquida na XP (R$)", "Captação na semana na XP (R$)",
    "Cruzamento com a XP", "Fundo da XP no cruzamento",
})


def _cruzamento_com_a_xp(fundo: dict) -> str:
    """"mesmo fundo", "pelo espelho", "pelo master", "por 2 fundos do mesmo
    master" ou "a XP não distribui"."""
    if not fundo.get("xp_ligacao"):
        return "a XP não distribui"
    return na_xp.via(fundo) or na_xp.LIGACAO_MESMO_FUNDO


def _fundos_da_xp(fundo: dict) -> str:
    """Os fundos da XP que medem a linha, quando nao e o proprio fundo."""
    if fundo.get("xp_ligacao") != na_xp.LIGACAO_MESMO_MASTER:
        return ""
    return "; ".join(
        f"{nome_de_fundo(v.get('nome') or '') or '-'} ({format_cnpj(v['cnpj'])})"
        for v in fundo.get("xp_fundos") or []
    )


def _flow_row(fundo: dict) -> list:
    return [
        nome_de_fundo(fundo["nome"]),
        format_cnpj(fundo["cnpj"]),
        fundo["categoria"],
        fundo.get("gestora") or "",
        ", ".join(fundo["vitrines"]),
        ", ".join(_platform_label(pid) for pid in fundo.get("proprio_de") or []),
        "sim" if fundo["exclusivo"] else "",
        fundo["captacao_bruta"],
        fundo["resgates"],
        fundo["captacao_liquida"],
        # A semana de referencia do digest (`recorte_semanal` do bloco).
        fundo.get("captacao_semana"),
        # A captacao na XP nas mesmas janelas (`com_captacao_na_xp`).
        fundo.get("captacao_xp_3m"),
        fundo.get("captacao_xp_semana"),
        _cruzamento_com_a_xp(fundo),
        _fundos_da_xp(fundo),
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
        # A rentabilidade de referencia (`com_rentabilidade`), na convencao de
        # lamina: base no fechamento da competencia anterior a janela.
        fundo.get("retorno_12m"),
        fundo.get("mediana_12m"),
        fundo.get("dias"),
        nome_da_tendencia(fundo.get("tendencia")),
        nome_da_tendencia(fundo.get("ritmo_semanal")),
        "sim" if fundo.get("estrutural") else "",
    ]


def _write_flow_rows(
    ws, fundos: list[dict], header_row: int, omitir: frozenset[str] = frozenset(),
    retornos: dict | None = None, captacao_xp: dict | None = None,
) -> int:
    """Escreve a tabela de fundos; devolve a linha seguinte a ultima.

    `omitir` sao cabecalhos de `_FLOW_HEADERS` que saem da tabela inteira;
    `retornos` (`performance.rentabilidade_por_cnpj`) preenche as colunas de
    rentabilidade de 12 meses; `captacao_xp` (`RunReport.captacao_xp`), as da
    captacao na XP.
    """
    if retornos:
        fundos = com_rentabilidade(fundos, retornos)
    fundos = na_xp.com_captacao_na_xp(fundos, captacao_xp)
    colunas = [
        (i, header) for i, header in enumerate(_FLOW_HEADERS) if header not in omitir
    ]
    for col_idx, (_, header) in enumerate(colunas, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(col_idx)].width = (
            46 if header in ("Fundo", "Fundo da XP no cruzamento")
            else 20 if header in ("CNPJ", "Gestora", "Cruzamento com a XP") else 16
        )
    ws.row_dimensions[header_row].height = 30

    row_idx = header_row + 1
    for fundo in fundos:
        linha = _flow_row(fundo)
        for col_idx, (origem, header) in enumerate(colunas, start=1):
            valor = linha[origem]
            cell = ws.cell(row=row_idx, column=col_idx, value=valor)
            cell.font = _BODY_FONT
            if header in _FLOW_MONEY:
                cell.number_format = _MONEY
            elif header in _FLOW_PCT:
                cell.number_format = _PCT_RETORNO
            elif header in _FLOW_INT:
                cell.number_format = _INT
            if header == _FLOW_LIQUIDA and isinstance(valor, (int, float)) and valor:
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
    "saida_persistente": "perdendo no último mês e na média anterior",
    "estavel": "sem mudança material no ritmo",
    "sem_serie": "competências de menos para falar em tendência",
}


def _quando(valor: str | None) -> str:
    """Dia em data brasileira, competencia (AAAAMM) como "jul/2026"."""
    if valor and len(valor) == 6 and valor.isdigit():
        return competencia_br(valor)
    return data_br(valor)


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
    url: str | None = None,
    janela: str | None = None,
    pregoes: int | None = None,
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
        if url:
            _link(sheet.cell(row=linha, column=3), url, url)
        campo("Fonte", fonte)
    if janela:
        campo("Janela", janela)
    campo("Período", f"de {_quando(de)} a {_quando(ate)}" if de and ate else "não informado")
    if pregoes:
        campo("Pregões", milhar(pregoes))
    if competencias:
        campo(
            "Competências",
            f"{len(competencias)} ({competencia_br(competencias[0])} a "
            f"{competencia_br(competencias[-1])})",
        )
    if ausentes:
        campo("Competências ausentes", ", ".join(competencia_br(m) for m in ausentes))
    if nota:
        sheet.cell(row=linha, column=1, value=nota).font = _NOTE_FONT
        linha += 1
    return linha + 1


def _add_captacao_casas_sheet(
    workbook: Workbook, flow: dict, suprimidos: dict[str, str] | None = None
) -> None:
    """Aba "Captacao por casa": de onde vem o saldo de cada vitrine.

    A leitura das superficies (as frases, a composicao, a familia e a tendencia
    por casa) com os numeros em celula, para conferir e filtrar. As frases sao
    as mesmas do digest e do dashboard: `leitura_da_captacao`.
    """
    suprimidos = suprimidos or {}
    sem_cnpj = suprimidos.get(BLOCO_CNPJ)
    sem_proprio = suprimidos.get(BLOCO_PRODUTO_PROPRIO)
    ws = workbook.create_sheet("Captação por casa")
    periodo = flow["periodo"]
    ws.cell(row=1, column=1, value="Captação dos fundos de vitrine - por casa").font = _TITLE_FONT
    linha = _periodo_bloco(
        ws, 2,
        de=periodo.get("de"), ate=periodo.get("ate"),
        competencias=periodo.get("meses_lidos") or [],
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Informe diário da CVM (INF_DIARIO)",
        url=CVM_INFORME_DIARIO.url,
        nota=(
            "A CVM publica fluxo por FUNDO, nunca por distribuidor: os números por "
            "casa são o fluxo dos fundos que ela exibe, no mercado inteiro. "
            "Movimento estrutural fica fora de todas as somas."
        ),
    )
    for frase in leitura_da_captacao(flow, suprimidos):
        ws.cell(row=linha, column=1, value=frase).font = _BODY_FONT
        linha += 1
    linha += 1

    def tabela(cabecalho: list[str], linhas: list[list], formatos: dict[int, str]) -> None:
        nonlocal linha
        for col, titulo in enumerate(cabecalho, start=1):
            ws.cell(row=linha, column=col, value=titulo)
        _style_header(ws, linha, len(cabecalho))
        linha += 1
        for valores in linhas:
            for col, valor in enumerate(valores, start=1):
                cell = ws.cell(row=linha, column=col, value=valor)
                if col in formatos:
                    cell.number_format = formatos[col]
            _style_row(ws, linha, len(cabecalho))
            linha += 1
        linha += 1

    # Composicao do saldo.
    ws.cell(row=linha, column=1, value="De onde vem o saldo").font = _HEADER_FONT_ESCURO
    linha += 1
    cabecalho = [
        "Casa", "Fundos na vitrine", "Fundos com informe", "Captação líquida (R$)",
        "PL de início (R$)", "Captação / PL de início (%)",
    ]
    if not sem_cnpj:
        cabecalho += ["Compartilhados (R$)", "Só nesta vitrine (R$)"]
    if not sem_proprio:
        cabecalho += [
            "Produto próprio (R$)", "Terceiros (R$)", "Sem gestora conhecida (fundos)",
        ]
    linhas = []
    for casa in flow["casas"]:
        valores = [
            casa["label"], casa["vitrine"], casa["com_informe"],
            casa["captacao_liquida"], casa.get("pl_inicio"), casa.get("sobre_pl"),
        ]
        if not sem_cnpj:
            valores += [casa.get("captacao_compartilhados"), casa["captacao_exclusivos"]]
        if not sem_proprio:
            valores += [
                casa.get("captacao_propria"), casa.get("captacao_terceiros"),
                casa.get("sem_gestora", 0),
            ]
        linhas.append(valores)
    formatos = {c: _MONEY for c, t in enumerate(cabecalho, start=1) if "(R$)" in t}
    formatos[6] = _PCT
    formatos.update({2: _INT, 3: _INT})
    if not sem_proprio:
        formatos[len(cabecalho)] = _INT
    tabela(cabecalho, linhas, formatos)
    if sem_proprio:
        ws.cell(
            row=linha - 1, column=1,
            value=f"Sem o corte produto próprio x terceiros: {sem_proprio}.",
        ).font = _NOTE_FONT
        linha += 1

    # Por familia de ativo.
    categorias = flow.get("por_categoria") or []
    if categorias:
        ws.cell(row=linha, column=1, value="Por família de ativo").font = _HEADER_FONT_ESCURO
        linha += 1
        ids = [c["platform_id"] for c in flow["casas"]]
        cabecalho = (
            ["Família"] + [f"{c['label']} (R$)" for c in flow["casas"]]
            + ["Todas as vitrines (R$)", "Fundos no universo"]
        )
        linhas = [
            [l["categoria"]]
            # Casa sem fundo da familia fica vazia, como o "-" do digest: zero
            # afirmaria um saldo nulo que ninguem mediu.
            + [
                l["casas"][pid]["captacao_liquida"]
                if l["casas"].get(pid, {}).get("fundos") else None
                for pid in ids
            ]
            + [l["universo"]["captacao_liquida"], l["universo"]["fundos"]]
            for l in categorias
        ]
        formatos = {c: _MONEY for c in range(2, len(ids) + 3)}
        formatos[len(cabecalho)] = _INT
        tabela(cabecalho, linhas, formatos)

    # Tendencia por casa, em fundos (a planilha guarda a contagem; a parcela e conta).
    por_casa = flow.get("tendencia_por_casa") or {}
    if por_casa:
        ws.cell(row=linha, column=1, value="Tendência dos fundos, por casa").font = _HEADER_FONT_ESCURO
        linha += 1
        estados = list(TREND_LABELS)
        cabecalho = ["Casa", *[nome_da_tendencia(e) for e in estados], "Total"]
        linhas = []
        for casa in flow["casas"]:
            contagem = por_casa.get(casa["platform_id"]) or {}
            linhas.append(
                [casa["label"], *[contagem.get(e, 0) for e in estados],
                 sum(contagem.values())]
            )
        tabela(cabecalho, linhas, {c: _INT for c in range(2, len(cabecalho) + 1)})
        # De onde vem a linha da XP: a lista distribuivel ou a vitrine (18/10/2026).
        ritmo_da_base = (flow.get("ritmo_da_base") or {}).get("nota")
        if ritmo_da_base:
            ws.cell(row=linha - 1, column=1, value=f"{ritmo_da_base}.").font = _NOTE_FONT
            linha += 1
        for estado in estados:
            ws.cell(row=linha, column=1, value=nome_da_tendencia(estado)).font = _BODY_FONT
            ws.cell(row=linha, column=2, value=TREND_LABELS[estado]).font = _NOTE_FONT
            linha += 1

    ws.column_dimensions["A"].width = 26
    for col in range(2, 13):
        ws.column_dimensions[get_column_letter(col)].width = 18


def _add_flow_sheet(
    workbook: Workbook, flow: dict, omitir: frozenset[str] = frozenset(),
    retornos: dict | None = None, prazo_dos_retornos: str = "",
    captacao_xp: dict | None = None,
) -> None:
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

    `captacao_xp` (`RunReport.captacao_xp`) poe ao lado a captacao de cada fundo
    na XP, nas mesmas janelas (20/10/2026); sem as bases da XP, as colunas saem.
    """
    if not na_xp.com_numero(captacao_xp):
        omitir = omitir | _FLOW_NA_XP
    ws = workbook.create_sheet("Captação por fundo")
    periodo = flow["periodo"]
    competencias = periodo.get("meses_lidos") or []

    ws.cell(row=1, column=1, value="Captação dos fundos de vitrine").font = _TITLE_FONT
    _periodo_bloco(
        ws, 2,
        de=periodo.get("de"), ate=periodo.get("ate"),
        competencias=competencias,
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Informe diário da CVM (INF_DIARIO)",
        url=CVM_INFORME_DIARIO.url,
        nota=(
            "A CVM publica fluxo por FUNDO, nunca por distribuidor: um fundo em "
            "três vitrines entra inteiro nas três somas. "
            + ("" if "Exclusivo" in omitir else
               "A coluna Exclusivo marca onde a atribuição a uma casa é inequívoca. ")
            + "Uma linha do informe é uma "
            "SUBCLASSE: PL, cotistas e fluxo somam as subclasses da classe, e a "
            "rentabilidade é a média dos retornos delas ponderada pelo PL. "
            "Rentabilidade é da cota patrimonial, ex-provento, com base no PRIMEIRO "
            "PREGÃO do período - as abas 'Rentabilidade 12 meses' e "
            "'Rentabilidade por fundo' usam a cota do fim da competência anterior, "
            "que é a convenção de lâmina, e por isso o mesmo fundo aparece com "
            "número ligeiramente diferente nelas."
            + (f" {prazo_dos_retornos}" if prazo_dos_retornos else "")
            + "".join(f" {nota}" for nota in na_xp.notas(captacao_xp))
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
    ultima = _write_flow_rows(ws, fundos, cabecalho, omitir, retornos, captacao_xp)
    ws.freeze_panes = f"A{cabecalho + 1}"
    if ultima > cabecalho + 1:
        ultima_coluna = get_column_letter(
            sum(1 for h in _FLOW_HEADERS if h not in omitir)
        )
        ws.auto_filter.ref = f"A{cabecalho}:{ultima_coluna}{ultima - 1}"

    linha = ultima + 1
    ws.cell(row=linha, column=1, value="Legenda da coluna Tendência").font = _HEADER_FONT
    linha += 1
    rotulos = flow.get("tendencia_labels") or _TENDENCIA_FALLBACK
    for chave, texto in rotulos.items():
        ws.cell(row=linha, column=1, value=nome_da_tendencia(chave)).font = _BODY_FONT
        ws.cell(row=linha, column=2, value=texto).font = _NOTE_FONT
        linha += 1


def _add_trend_sheet(workbook: Workbook, flow: dict) -> None:
    """Aba "Tendencia de captacao": a serie mensal, por casa e por fundo.

    A coluna que importa e a media diaria, nao o total do mes: o mes corrente
    fecha com menos pregoes que os cheios, e comparar totais marcaria todo fundo
    como desacelerando por um efeito de calendario.
    """
    ws = workbook.create_sheet("Tendência de captação")
    competencias = flow.get("competencias") or []

    ws.cell(row=1, column=1, value="Tendência de captação").font = _TITLE_FONT
    ws.cell(
        row=2, column=1,
        value=(
            "Média diária de captação líquida por competência. A média diária "
            "existe porque o mês corrente tem menos pregões que os fechados - "
            "comparar totais brutos seria comparar calendários."
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
    ws.cell(row=linha, column=1, value="Fundos por tendência").font = _HEADER_FONT
    linha += 1
    for chave, quantidade in resumo.items():
        ws.cell(row=linha, column=1, value=nome_da_tendencia(chave)).font = _BODY_FONT
        ws.cell(row=linha, column=2, value=quantidade).font = _BODY_FONT
        ws.cell(row=linha, column=3, value=rotulos.get(chave, "")).font = _NOTE_FONT
        linha += 1

    # As tres listas que sairam do digest em 14/10/2026 continuam aqui.
    base = _platform_label(flow.get("baseline_id") or "xp")
    for titulo, chave in (
        (f"Na vitrine da {base}, perdendo dinheiro e cotistas", "baseline_perdendo"),
        ("Acelerando - captando mais por dia que antes", "acelerando"),
        ("Viraram saída - captavam e passaram a perder", "virou_saida"),
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
            ws.cell(row=linha, column=1, value=nome_de_fundo(fundo["nome"])).font = _BODY_FONT
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


def _add_grade_sheet(
    workbook: Workbook, grade: dict, retornos: dict | None = None,
    prazo_dos_retornos: str = "",
) -> None:
    """Aba "Oportunidades de grade": o que capta fora da vitrine da XP."""
    ws = workbook.create_sheet("Oportunidades de grade")

    ws.cell(row=1, column=1, value="Oportunidades de grade para a XP").font = _TITLE_FONT
    ws.cell(
        row=2, column=1,
        value=(
            f"{grade['total']} fundos captam varejo fora da vitrine da XP; "
            f"{grade['total_produto_do_concorrente']} são produto próprio da casa que "
            f"os exibe e não se transferem. Restam {grade['total_transferiveis']} "
            f"({grade['total_operacionais']} de gestora já distribuída pela XP, "
            f"{grade['total_comerciais']} exigem abrir gestora)."
            + (f" {prazo_dos_retornos}" if prazo_dos_retornos else "")
        ),
    ).font = _NOTE_FONT

    linha = 4
    for titulo, chave in (
        ("Inclusão operacional - gestora já distribuída pela XP", "operacionais"),
        ("Exige abrir gestora", "comerciais"),
        ("Mais varejo - por cotistas trazidos", "mais_varejo"),
        ("Produto próprio do concorrente - não transferível", "produto_do_concorrente"),
        ("Na vitrine da XP e perdendo varejo", "na_vitrine_perdendo"),
    ):
        fundos = grade.get(chave) or []
        if not fundos:
            continue
        ws.cell(row=linha, column=1, value=titulo).font = _HEADER_FONT
        linha = _write_flow_rows(
            ws, fundos, linha + 1, omitir=_FLOW_NA_XP, retornos=retornos
        ) + 1


# --- rentabilidade ponderada --------------------------------------------------

_PERF_HEADERS = [
    "Casa", "Categoria", "Fundos", "Mediana dos fundos (%)", "Ponderada por PL (%)",
    "Ponderada por cotistas (%)", "PL menos cotistas (pp)",
    "Mediana da categoria no mercado (%)",
    "PL (R$)", "Cotistas",
]


_PERF_LARGURAS = {
    "Casa": 22, "Categoria": 40, "Fundos": 9, "Mediana dos fundos (%)": 12,
    "Ponderada por PL (%)": 12, "Ponderada por cotistas (%)": 14,
    "PL menos cotistas (pp)": 18, "Mediana da categoria no mercado (%)": 26,
    "PL (R$)": 18, "Cotistas": 12,
}
_PERF_POR_COTISTAS = frozenset({
    "Ponderada por cotistas (%)", "PL menos cotistas (pp)", "% dos cotistas",
    "Índice cotistas", "Cotistas",
})


def _linha_por_cabecalho(ws, linha: int, cabecalhos: list[str], valores: dict) -> None:
    for col, titulo in enumerate(cabecalhos, start=1):
        cell = ws.cell(row=linha, column=col, value=valores[titulo])
        if titulo in _PERF_FORMATOS:
            cell.number_format = _PERF_FORMATOS[titulo]


def _add_performance_sheet(
    workbook: Workbook, performance: dict, sem_cotistas: str | None = None
) -> None:
    """Aba "Rentabilidade <janela>": as tres leituras, por casa e por categoria.

    Uma aba por janela - "Rentabilidade 12 meses" e "Rentabilidade 3 meses" -
    com o prazo no nome, porque as duas tem a mesma forma e so o prazo as
    separa. A tabela de cima responde pela vitrine inteira; a de baixo, dentro de
    cada categoria - e e nela que o gap fica legivel, porque na vitrine inteira
    a diferenca entre as ponderadas mistura composicao com desempenho.
    """
    janela = janela_do_bloco(performance)
    rotulo = janela["rotulo"]
    ws = workbook.create_sheet(f"Rentabilidade {rotulo}")
    periodo = performance["periodo"]
    universo = performance["universo"]

    if janela.get("serie") == "mensal":
        base = (
            "Base na cota do FECHAMENTO da competência anterior à janela, a "
            "convenção de lâmina - a mesma da coluna '12 meses' da aba "
            "'Rentabilidade por fundo'. A aba 'Rentabilidade 3 meses' parte do "
            "primeiro pregão do período. Janela longa separa quem ficou para "
            "trás: em três meses dominados pelo CDI, quase tudo fica a centésimos "
            "da mediana da categoria. Fundo que não existia no começo da janela "
            "fica de fora, na vitrine e na mediana de referência."
        )
    else:
        base = (
            "Base na cota do PRIMEIRO PREGÃO do período - esta aba responde o "
            "retorno ENTRE DUAS DATAS. As abas 'Rentabilidade 12 meses' e "
            "'Rentabilidade por fundo' usam o fechamento da competência anterior, "
            "que é a convenção de lâmina, e por isso o mesmo fundo sai com número "
            "ligeiramente diferente lá."
        )

    ws.cell(
        row=1, column=1, value=f"Rentabilidade da vitrine em {rotulo}"
    ).font = _TITLE_FONT
    linha = _periodo_bloco(
        ws, 2,
        de=janela.get("de"), ate=janela.get("ate"),
        competencias=periodo.get("meses_lidos") or [],
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Cota patrimonial do informe diário da CVM (INF_DIARIO)",
        url=CVM_INFORME_DIARIO.url,
        janela=f"{rotulo}, base na {janela['base']}",
        pregoes=janela.get("pregoes"),
        nota=(
            f"{base} Referência: mediana da mesma categoria no universo oficial, "
            f"no mesmo prazo ({milhar(universo['fundos_com_serie'])} fundos, "
            f"{universo['categorias_com_referencia']} categorias com amostra "
            f">= {universo['amostra_minima']}). É o retorno dos fundos que a casa "
            "exibe, com o peso que eles têm no MERCADO - não o retorno dos clientes "
            "dela: a CVM publica cota, PL e cotistas por fundo, somando todos os "
            "distribuidores. Cota ex-provento. ÍNDICE = share do PL nos fundos "
            "abaixo da mediana dividido pelo share de fundos abaixo: abaixo de 1, o "
            "dinheiro EVITA os piores da categoria; acima de 1, se concentra neles. "
            "Cerca de metade de qualquer universo fica abaixo da mediana por "
            "construção, então a contagem de fundos sozinha não é sinal de nada."
        ),
    )
    omitir = _PERF_POR_COTISTAS if sem_cotistas else frozenset()
    if sem_cotistas:
        ws.cell(
            row=linha - 1, column=1,
            value=f"Sem as leituras por cotistas: {sem_cotistas}.",
        ).font = _NOTE_FONT
        linha += 1
    resumo = [
        "Casa", "Fundos com série", "Vitrine", "Cobertura",
        "Mediana dos fundos (%)", "Ponderada por PL (%)", "Ponderada por cotistas (%)",
        "PL menos cotistas (pp)",
        "Fundos abaixo da mediana", "% dos fundos", "% do PL", "% dos cotistas",
        "Índice PL", "Índice cotistas",
    ]
    resumo = [t for t in resumo if t not in omitir]
    for col, titulo in enumerate(resumo, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(resumo))
    linha += 1
    for casa in performance["casas"]:
        massa = casa["alocacao_abaixo_da_mediana"]
        _linha_por_cabecalho(ws, linha, resumo, {
            "Casa": casa["label"], "Fundos com série": casa["com_serie"],
            "Vitrine": casa["vitrine"], "Cobertura": casa["cobertura"],
            "Mediana dos fundos (%)": casa["mediana"],
            "Ponderada por PL (%)": casa["por_pl"],
            "Ponderada por cotistas (%)": casa["por_cotista"],
            "PL menos cotistas (pp)": casa["gap"],
            "Fundos abaixo da mediana": f"{massa['fundos']} de {massa['de']}",
            "% dos fundos": massa["share_fundos"], "% do PL": massa["share_pl"],
            "% dos cotistas": massa["share_cotistas"],
            "Índice PL": massa["indice_pl"], "Índice cotistas": massa["indice_cotistas"],
        })
        _style_row(ws, linha, len(resumo))
        linha += 1

    linha += 1
    ws.cell(row=linha, column=1, value=f"Por categoria, em {rotulo}").font = _TITLE_FONT
    linha += 1
    por_categoria = [t for t in _PERF_HEADERS if t not in omitir]
    for col, titulo in enumerate(por_categoria, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(por_categoria))
    cabecalho = linha
    linha += 1
    for item in performance["por_categoria"]:
        _linha_por_cabecalho(ws, linha, por_categoria, {
            "Casa": item["label"], "Categoria": item["categoria"],
            "Fundos": item["fundos"], "Mediana dos fundos (%)": item["mediana"],
            "Ponderada por PL (%)": item["por_pl"],
            "Ponderada por cotistas (%)": item["por_cotista"],
            "PL menos cotistas (pp)": item["gap"],
            "Mediana da categoria no mercado (%)": item["referencia"],
            "PL (R$)": item["pl"], "Cotistas": item["cotistas"],
        })
        _style_row(ws, linha, len(por_categoria))
        linha += 1

    ws.freeze_panes = f"A{cabecalho + 1}"
    if linha > cabecalho + 1:
        ws.auto_filter.ref = (
            f"A{cabecalho}:{get_column_letter(len(por_categoria))}{linha - 1}"
        )
    larguras = [_PERF_LARGURAS[t] for t in por_categoria] + [18]
    for col, largura in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(col)].width = largura


def _retornos_por_cnpj(bloco: dict | None) -> dict[str, dict]:
    """CNPJ -> retorno, mediana e excesso do fundo na janela do bloco.

    Os objetos de `_casas` cobrem todos os fundos da vitrine; o JSON (bloco
    relido de disco) so traz os de `onde_esta_a_gente`.
    """
    if not bloco:
        return {}
    saida: dict[str, dict] = {}
    for casa in bloco.get("_casas") or []:
        for fundo in casa.fundos:
            saida.setdefault(fundo.cnpj, {
                "retorno": fundo.retorno,
                "mediana_categoria": fundo.mediana_categoria,
                "excesso": fundo.excesso,
            })
    for fundos in (bloco.get("onde_esta_a_gente") or {}).values():
        for fundo in fundos:
            saida.setdefault(fundo["cnpj"], fundo)
    return saida


def _add_gente_sheet(
    workbook: Workbook, performance: dict, outro: dict | None = None
) -> None:
    """Aba "Onde esta a gente": os fundos com mais cotistas, e como rendem.

    Ordenada por numero de cotistas, nao por PL nem por retorno: a duvida que
    ela responde nao e sobre o fundo medio da prateleira, e sim sobre aquele em
    que a maior parte das pessoas de fato esta.

    Os fundos e o retorno principal sao os da janela mais longa (12 meses);
    `outro` e a janela curta, nas colunas seguintes, pelo CNPJ. Cada coluna de
    retorno diz o prazo no proprio cabecalho.
    """
    janela = janela_do_bloco(performance)
    rotulo = janela["rotulo"]
    ws = workbook.create_sheet("Onde está a gente")
    periodo = performance.get("periodo") or {}
    ws.cell(
        row=1, column=1,
        value="Onde está a gente - fundos da vitrine com mais cotistas",
    ).font = _TITLE_FONT
    curta = janela_do_bloco(outro) if outro else None
    linha = _periodo_bloco(
        ws, 2,
        de=janela.get("de"), ate=janela.get("ate"),
        competencias=periodo.get("meses_lidos") or [],
        ausentes=periodo.get("meses_ausentes") or [],
        fonte="Cota patrimonial do informe diário da CVM (INF_DIARIO)",
        url=CVM_INFORME_DIARIO.url,
        janela=f"{rotulo}, base na {janela['base']}",
        pregoes=janela.get("pregoes"),
        nota=(
            f"Os fundos de cada vitrine com mais cotistas, com o retorno em {rotulo} "
            "ao lado da mediana da mesma categoria no mercado, no mesmo prazo"
            + (
                f"; nas colunas seguintes, o mesmo em {curta['rotulo']} "
                f"({periodo_br(curta.get('de'), curta.get('ate'))}, base na "
                f"{curta['base']})" if curta else ""
            )
            + ". Excesso negativo é um fundo de muita gente rendendo abaixo dos "
            "pares. Cotistas e PL são do fundo inteiro, somando todos os "
            "distribuidores."
        ),
    )

    # "no mercado" no cabecalho e nao so na nota: cotistas e PL sao do FUNDO
    # inteiro, somando todos os distribuidores. A CVM nao publica nada por
    # distribuidor. Sem isso no proprio rotulo, a coluna "Cotistas" ao lado da
    # coluna "Casa" se le como "os clientes dela". (titulo, formato, largura)
    cabecalhos: list[tuple[str, str | None, int]] = [
        ("Casa", None, 18), ("Fundo", None, 48), ("CNPJ", None, 20),
        ("Categoria", None, 34), ("Cotistas no mercado", _INT, 12),
        ("PL no mercado (R$)", _MONEY, 18), ("Ticket médio (R$)", _MONEY, 16),
        (f"Rentabilidade em {rotulo} (%)", _PCT, 16),
        (f"Mediana da categoria em {rotulo} (%)", _PCT, 20),
        (f"Excesso em {rotulo} (pp)", _PCT, 14),
    ]
    if curta:
        cabecalhos += [
            (f"Rentabilidade em {curta['rotulo']} (%)", _PCT, 16),
            (f"Mediana da categoria em {curta['rotulo']} (%)", _PCT, 20),
            (f"Excesso em {curta['rotulo']} (pp)", _PCT, 14),
        ]
    cabecalhos += [
        (f"Captação líquida no mercado em {rotulo} (R$)", _MONEY, 20),
        (f"Variação cotistas no mercado em {rotulo}", _INT, 16),
        (f"Pregões em {rotulo}", _INT, 12),
    ]
    for col, (titulo, _, _) in enumerate(cabecalhos, start=1):
        ws.cell(row=linha, column=col, value=titulo)
        if titulo.startswith("Captação líquida no mercado"):
            _comentar(ws.cell(row=linha, column=col), DEFINICAO_CAPTACAO_NO_MERCADO)
    _style_header(ws, linha, len(cabecalhos))
    cabecalho_gente = linha
    linha += 1

    da_curta = _retornos_por_cnpj(outro)
    rotulos = {c["platform_id"]: c["label"] for c in performance["casas"]}
    for platform_id, fundos in performance["onde_esta_a_gente"].items():
        for fundo in fundos:
            valores = [
                rotulos.get(platform_id, platform_id), nome_de_fundo(fundo["nome"]),
                format_cnpj(fundo["cnpj"]), fundo["categoria"], fundo["cotistas"],
                fundo["pl"], fundo["ticket_medio"], fundo["retorno"],
                fundo["mediana_categoria"], fundo["excesso"],
            ]
            if curta:
                par = da_curta.get(fundo["cnpj"]) or {}
                valores += [
                    par.get("retorno"), par.get("mediana_categoria"), par.get("excesso"),
                ]
            valores += [
                fundo["captacao_liquida"], fundo["variacao_cotistas"], fundo["dias"],
            ]
            for col, (valor, (_, formato, _)) in enumerate(zip(valores, cabecalhos), start=1):
                cell = ws.cell(row=linha, column=col, value=valor)
                if formato:
                    cell.number_format = formato
            ws.cell(row=linha, column=3).alignment = Alignment(horizontal="left")
            _style_row(ws, linha, len(cabecalhos))
            linha += 1

    ultima = get_column_letter(len(cabecalhos))
    ws.freeze_panes = f"A{cabecalho_gente + 1}"
    if linha > cabecalho_gente + 1:
        ws.auto_filter.ref = f"A{cabecalho_gente}:{ultima}{linha - 1}"
    for col, (_, _, largura) in enumerate(cabecalhos, start=1):
        ws.column_dimensions[get_column_letter(col)].width = largura


# --- custo e mandato ----------------------------------------------------------

_CUSTO_HEADERS = [
    "Casa", "Fundo", "CNPJ", "Categoria", "Taxa adm (%)",
    "Mediana da categoria (%)", "Índice", "Faixa", "Idade da entrega (meses)",
    "Cobra entrada", "Cobra saída", "Custódia máx. (%)", "Dias de cotização",
    "Mínimo da classe (R$)", "Menor porta do mesmo fundo (R$)",
    "Fora do índice porque",
]

_INDICE_FMT = '0.00"x";-;-'
_SHARE_FMT = '0.0%;-;-'

# Formato de cada coluna das abas de rentabilidade, por cabecalho - e o
# que deixa a previdencia tirar as colunas de cotistas sem desalinhar formato.
# Mora aqui, e nao junto da aba, porque depende de _INDICE_FMT.
_PERF_FORMATOS = {
    "Cobertura": _SHARE_FMT,
    "Mediana dos fundos (%)": _PCT,
    "Ponderada por PL (%)": _PCT,
    "Ponderada por cotistas (%)": _PCT,
    "PL menos cotistas (pp)": _PCT,
    "% dos fundos": _SHARE_FMT,
    "% do PL": _SHARE_FMT,
    "% dos cotistas": _SHARE_FMT,
    "Índice PL": _INDICE_FMT,
    "Índice cotistas": _INDICE_FMT,
    "Mediana da categoria no mercado (%)": _PCT,
    "PL (R$)": _MONEY,
    "Cotistas": _INT,
}


# Como a coluna "Base da prateleira" escreve `CustoCasa.fonte`.
_BASE_DA_PRATELEIRA = {
    "vitrine": "vitrine do site",
    "base_interna": "base interna (lista distribuível)",
}
_CUSTO_POR_CLASSE_HEADERS = [
    "Casa", "Classe", "Fundos na prateleira", "Comparáveis", "Índice mediano",
    "Taxa mediana (% a.a.)", "Caros", "Baratos", "Base da prateleira",
]


def _add_custo_por_classe_sheet(workbook: Workbook, custo: dict) -> None:
    """Aba "Custo por classe": o custo de cada casa em cada classe de fundo.

    Pedido de 16/10/2026. Todas as classes de cada casa, inclusive as sem
    indice - a planilha e a versao completa; o digest e o painel mostram so as
    classes com indice. Aba propria porque a lista de fundos de "Custo e
    acesso" congela o cabecalho dela, e uma tabela de ~30 linhas acima o
    empurraria para fora da tela.
    """
    ws = workbook.create_sheet("Custo por classe")
    ws.cell(row=1, column=1, value="Custo por classe de fundo").font = _TITLE_FONT
    notas = [
        "A mesma conta do custo da casa - mediana dos índices (taxa do fundo ÷ mediana "
        "da categoria dele), não das taxas - dentro de cada classe. A taxa mediana é "
        "referência: dentro da classe o mix pesa menos, mas ainda pesa (renda fixa "
        "simples contra crédito livre). Fonte e regras na aba Custo e acesso.",
        nota_das_classes(custo["casas"]),
    ]
    if custo.get("nota_prateleira"):
        notas.append(custo["nota_prateleira"])
    for linha, nota in enumerate(notas, start=2):
        ws.cell(row=linha, column=1, value=nota).font = _NOTE_FONT

    cabecalho = len(notas) + 3
    for col, titulo in enumerate(_CUSTO_POR_CLASSE_HEADERS, start=1):
        ws.cell(row=cabecalho, column=col, value=titulo)
    _style_header(ws, cabecalho, len(_CUSTO_POR_CLASSE_HEADERS))
    linha = cabecalho + 1
    for casa in custo["casas"]:
        for classe in casa.get("por_classe") or []:
            valores = [
                casa["label"], classe["classe"], classe["fundos"], classe["comparaveis"],
                classe["indice_mediano"], classe["taxa_mediana"], classe["share_caro"],
                classe["share_barato"],
                _BASE_DA_PRATELEIRA.get(casa.get("fonte"), "vitrine do site"),
            ]
            for col, valor in enumerate(valores, start=1):
                ws.cell(row=linha, column=col, value=valor)
            ws.cell(row=linha, column=5).number_format = _INDICE_FMT
            ws.cell(row=linha, column=6).number_format = _PCT
            for col in (7, 8):
                ws.cell(row=linha, column=col).number_format = _SHARE_FMT
            _style_row(ws, linha, len(_CUSTO_POR_CLASSE_HEADERS))
            linha += 1

    ws.freeze_panes = f"A{cabecalho + 1}"
    if linha > cabecalho + 1:
        ultima = get_column_letter(len(_CUSTO_POR_CLASSE_HEADERS))
        ws.auto_filter.ref = f"A{cabecalho}:{ultima}{linha - 1}"
    for col, largura in zip("ABCDEFGHI", (18, 20, 14, 13, 14, 14, 10, 10, 30)):
        ws.column_dimensions[col].width = largura


def _add_custo_sheet(workbook: Workbook, custo: dict) -> None:
    """Aba "Custo e acesso": a taxa de cada fundo contra a categoria dele.

    A coluna que importa e Indice, nao Taxa: comparar a taxa bruta entre casas
    mede o MIX da prateleira (renda fixa custa ~0,2% ao ano, acoes ~2%), nao o
    preco dos produtos. Os fundos que ficaram fora do indice aparecem com o
    motivo na ultima coluna, em vez de sumirem da tabela.
    """
    ws = workbook.create_sheet("Custo e acesso")
    universo = custo["universo"]

    ws.cell(row=1, column=1, value="Custo da prateleira").font = _TITLE_FONT
    ws.cell(row=2, column=1, value="Fonte").font = _HEADER_FONT
    ws.cell(
        row=2, column=2,
        value="Extrato de informações da CVM (EXTRATO) - uma entrega por classe",
    ).font = _BODY_FONT
    _link(ws.cell(row=2, column=3), CVM_EXTRATO.url, CVM_EXTRATO.url)
    ws.cell(row=3, column=1, value="Período").font = _HEADER_FONT
    ws.cell(
        row=3, column=2,
        value=(
            "Não é um intervalo: cada classe tem a data-base da própria entrega, "
            f"e só entram as de até {universo['idade_maxima_meses']} meses. A "
            "coluna 'Idade da entrega' diz a de cada fundo."
        ),
    ).font = _BODY_FONT
    ws.cell(
        row=4, column=1,
        value=(
            f"Extrato de informações da CVM: {milhar(universo['classes_na_base'])} classes, "
            f"{universo['categorias_com_referencia']} categorias com amostra "
            f">= {universo['amostra_minima']} para servir de referência. Entram só "
            "taxas em percentual ao ano (o mesmo campo carrega valor em reais e "
            f"texto livre) com entrega de até {universo['idade_maxima_meses']} "
            "meses. Taxa é propriedade da classe, idêntica em qualquer "
            "distribuidor - por isso pode vir da base oficial. Aplicação mínima "
            "NÃO: o mínimo do registro é o institucional da classe, não o ticket "
            "que o site cobra."
        ),
    ).font = _NOTE_FONT
    # De onde sai a prateleira de cada casa: a XP pela base interna (16/10/2026).
    if custo.get("nota_prateleira"):
        ws.cell(row=5, column=1, value=custo["nota_prateleira"]).font = _NOTE_FONT

    linha = 7
    resumo = [
        "Casa", "Comparáveis", "Fundos na prateleira", "Índice mediano", "Caros", "Baratos",
        "Sem taxa de entrada/saída", "Resgate até D+1", "Com porta menor",
        "Base da prateleira",
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
            casa["com_porta_menor"], _BASE_DA_PRATELEIRA.get(casa.get("fonte"), "vitrine do site"),
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
            rotulos.get(platform_id, ""), nome_de_fundo(fundo["nome"]),
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
        value="Extrato de informações da CVM (EXTRATO) - política de investimento",
    ).font = _BODY_FONT
    _link(ws.cell(row=2, column=3), CVM_EXTRATO.url, CVM_EXTRATO.url)
    ws.cell(row=3, column=1, value="Período").font = _HEADER_FONT
    ws.cell(
        row=3, column=2,
        value=(
            "Não se aplica: mandato é condição vigente do regulamento, não medida "
            "de um intervalo."
        ),
    ).font = _BODY_FONT
    ws.cell(row=4, column=1, value=mandato["nota"]).font = _NOTE_FONT

    linha = 6
    cabecalho = [
        "Casa", "Dimensão", "Share da vitrine", "Share das concorrentes",
        "Índice", "Aposta", "Teto mediano (%)",
    ]
    for col, titulo in enumerate(cabecalho, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(cabecalho))
    inicio = linha
    linha += 1
    for item in mandato["linhas"]:
        valores = [
            item["label"], item["rotulo"], item["share"], item["share_pares"],
            "só ela" if item["so_ela"] else item["indice"],
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
        fonte="Cota patrimonial do informe diário da CVM (INF_DIARIO)",
        url=CVM_INFORME_DIARIO.url,
        nota=(
            "Retorno DO PERÍODO de cada janela, não anualizado - 28% em 24 meses "
            "e menos, ao ano, que 12% em 12. A base de cada janela é a cota do "
            "fim da competência ANTERIOR a ela; quando o fundo não existia lá, e "
            "a primeira cota observada dentro da janela. Cota ex-provento: fundo "
            "que distribui aparece abaixo do que o investidor recebeu. PL e "
            "cotistas são do fundo inteiro, somando todos os distribuidores. "
            "As abas 'Rentabilidade 3 meses' e 'Captação por fundo' usam outra "
            "base - o primeiro pregão do período - porque respondem outra "
            "pergunta: o retorno ENTRE DUAS DATAS. Esta aqui, como a "
            "'Rentabilidade 12 meses', responde o retorno DA COMPETÊNCIA, que é "
            "a convenção de lâmina. As duas estão certas "
            "e o mesmo fundo sai com número ligeiramente diferente nas duas; a "
            "base de cada uma está escrita na aba Metodologia."
        ),
    )

    ws.cell(row=linha, column=1, value="Janelas").font = _HEADER_FONT
    linha += 1
    for j in janelas:
        ws.cell(row=linha, column=1, value=j["rotulo"]).font = _BODY_FONT
        ws.cell(
            row=linha, column=2,
            value=(
                f"{competencia_br(j['de'])} a {competencia_br(j['ate'])} - "
                f"{contagem(j['competencias'], 'competência', 'competências')}"
            ),
        ).font = _BODY_FONT
        linha += 1
    linha += 1

    # Resumo por casa: mediana, ponderada por PL e por cotistas em cada janela.
    resumo = ["Casa", "Fundos com série", "Vitrine", "Leitura"] + rotulos
    for col, titulo in enumerate(resumo, start=1):
        ws.cell(row=linha, column=col, value=titulo)
    _style_header(ws, linha, len(resumo))
    linha += 1
    for casa in rent.get("casas") or []:
        for leitura, chave in (
            ("Mediana dos fundos", "mediana"),
            ("Ponderada por PL", "por_pl"),
            ("Ponderada por cotistas", "por_cotista"),
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
        + ["Subclasses", "Competências na janela de 24m"]
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
            nome_de_fundo(fundo["nome"]), format_cnpj(fundo["cnpj"]), fundo.get("categoria") or "",
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
