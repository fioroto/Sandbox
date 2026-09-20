"""Relatórios e exportação de dados de captação semanal de ETFs.

Gera tabelas ricas no terminal via `rich` e cria planilhas Excel formatadas (.xlsx)
com matrizes dinâmicas por categoria, por ETF e por semana ISO.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from rich.console import Console
from rich.table import Table

from shelf_monitor.market.etf import EtfMetadata, catalog_counts
from shelf_monitor.market.etf_flow import CategoryWeeklySummary, EtfWeeklyFlow, FlowCoverage
from shelf_monitor.market.fie_balancete import EtfMonthlyPl, PlCoverage
from shelf_monitor.normalize import format_cnpj

_KIND_LABEL = {"etf": "ETF", "feeder": "Feeder"}

FONT = "Arial"
_HEADER_FILL = PatternFill("solid", fgColor="1F3864")
_HEADER_FONT = Font(name=FONT, size=10, bold=True, color="FFFFFF")
_TITLE_FONT = Font(name=FONT, size=13, bold=True)
_BODY_FONT = Font(name=FONT, size=10)
_NOTE_FONT = Font(name=FONT, size=9, italic=True, color="595959")

_GREEN_FILL = PatternFill("solid", fgColor="E2EFDA")
_RED_FILL = PatternFill("solid", fgColor="FCE4D6")
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(bottom=_THIN)

_MONEY = "#,##0;(#,##0);-"
_PCT = '0.00"%";-;-'
_INT = "0;-;-"


def _format_rentab(flow: EtfWeeklyFlow) -> str:
    """"suspeito" e "-" são coisas diferentes: a primeira é um número que
    existia e foi descartado por implausível; a segunda é ausência de cota
    para calcular. Misturar as duas esconderia justamente o alerta que a
    guarda de sanidade existe para dar.
    """
    if flow.data_suspeita:
        return "suspeito"
    if flow.rentabilidade_semana is None:
        return "-"
    return f"{flow.rentabilidade_semana:+.2f}%"


def format_money_millions(val: float) -> str:
    """Formata valor em milhões (ex: R$ +12,4M ou R$ -3,1M)."""
    val_m = val / 1_000_000.0
    if abs(val_m) < 0.01:
        return "R$ 0,0M"
    sign = "+" if val_m > 0 else ""
    return f"R$ {sign}{val_m:,.1f}M".replace(",", "X").replace(".", ",").replace("X", ".")


def print_etf_summary(
    weekly_flows: list[EtfWeeklyFlow],
    category_summaries: list[CategoryWeeklySummary],
    catalog: dict[str, EtfMetadata],
    coverage: FlowCoverage | None = None,
    source_label: str = "INF_DIARIO",
) -> None:
    """Exibe no terminal a visão resumida da captação de ETFs.

    O catálogo (quantos ETFs existem, por categoria) sai sempre - é a resposta
    a "o monitor enxerga todos os ETFs do mercado?" - mesmo quando não há
    fluxo semanal para mostrar. Sem essa separação, uma falha no download do
    INF_DIARIO parecia "não há ETF nenhum", quando na verdade o catálogo
    estava completo e só a captação é que não veio.

    `source_label` diz de onde o fluxo veio (INF_DIARIO ou Fundos.NET): a
    ressalva de cobertura do INF_DIARIO só faz sentido para ele.
    """
    console = Console()
    is_inf_diario = source_label.upper() == "INF_DIARIO"

    counts = catalog_counts(catalog)
    console.print()
    console.print("[bold cyan]=== CATÁLOGO DE ETFs NA CVM ===[/]")
    console.print(
        f"[dim]{counts['etfs']} ETFs (FIIM) cadastrados e ativos, "
        f"{counts['feeders']} feeders (FIF que apenas replicam um ETF)[/]"
    )
    if counts["por_categoria"]:
        by_cat = sorted(counts["por_categoria"].items(), key=lambda kv: -kv[1])
        console.print("[dim]Por categoria: " + ", ".join(f"{cat} {n}" for cat, n in by_cat) + "[/]")
    console.print()

    if coverage is not None and coverage.etfs_no_catalogo:
        pct = coverage.etf_coverage_ratio * 100
        style = "yellow" if pct < 50 else "green"
        line = (
            f"[{style}]Cobertura de fluxo ({source_label}): {coverage.etfs_com_fluxo} de "
            f"{coverage.etfs_no_catalogo} ETFs ({pct:.0f}%).[/]"
        )
        if is_inf_diario:
            line += (
                " [dim]O informe diário cobre FI/FIF, não FIIM - a captação de ETF de "
                "verdade vem do Fundos.NET (`etf --probe-sources`).[/]"
            )
        console.print(line)
        if coverage.feeders_no_catalogo:
            console.print(
                f"[dim]Feeders com fluxo: {coverage.feeders_com_fluxo} de "
                f"{coverage.feeders_no_catalogo}[/]"
            )
        if coverage.semanas_suspeitas:
            console.print(
                f"[yellow]{coverage.semanas_suspeitas} semana(s) com rentabilidade "
                "descartada por implausível - ver 'Dado suspeito' na planilha.[/]"
            )
        console.print()

    if not weekly_flows:
        console.print(f"[yellow]Nenhum dado de captação semanal encontrado (fluxo: {source_label}).[/]")
        return

    # Descobrir a última semana disponível
    latest_week = max(f.week_key for f in weekly_flows)
    latest_flows = [f for f in weekly_flows if f.week_key == latest_week]
    latest_cats = [c for c in category_summaries if c.week_key == latest_week]

    console.print(f"[bold cyan]=== FLUXO SEMANAL (Semana {latest_week}) ===[/]")
    console.print(f"[dim]ETFs com movimento na semana: {len(latest_flows)}[/]\n")

    # Tabela 1: Resumo por Categoria
    cat_table = Table(title=f"Captação por Categoria de ETF ({latest_week})", title_style="bold green")
    cat_table.add_column("Categoria")
    cat_table.add_column("ETFs", justify="right")
    cat_table.add_column("Captação Bruta", justify="right")
    cat_table.add_column("Resgates", justify="right")
    cat_table.add_column("Captação Líquida", justify="right", style="bold")
    cat_table.add_column("PL Total", justify="right")

    for cat in sorted(latest_cats, key=lambda c: c.captacao_liquida, reverse=True):
        style = "green" if cat.captacao_liquida > 0 else ("red" if cat.captacao_liquida < 0 else "dim")
        cat_table.add_row(
            cat.category,
            str(cat.qtd_etfs),
            format_money_millions(cat.captacao_bruta),
            format_money_millions(cat.resgates),
            f"[{style}]{format_money_millions(cat.captacao_liquida)}[/]",
            format_money_millions(cat.pl_total),
        )

    console.print(cat_table)

    # Tabela 2: Top 10 Captação Líquida Positiva
    top_inflow = sorted([f for f in latest_flows if f.captacao_liquida > 0], key=lambda f: f.captacao_liquida, reverse=True)[:10]
    if top_inflow:
        in_table = Table(title=f"\nTop 10 Maior Captação Líquida ({latest_week})", title_style="bold green")
        in_table.add_column("Ticker / Nome", style="cyan")
        in_table.add_column("Tipo")
        in_table.add_column("Categoria")
        in_table.add_column("Gestora")
        in_table.add_column("Captação Líquida", justify="right", style="bold green")
        in_table.add_column("PL Fim", justify="right")
        in_table.add_column("Cotistas", justify="right")
        in_table.add_column("Retorno Semanal", justify="right")

        for f in top_inflow:
            name = f.ticker or f.etf_name[:30]
            in_table.add_row(
                name,
                _KIND_LABEL.get(f.kind, f.kind),
                f.category,
                (f.manager_name or "-")[:25],
                format_money_millions(f.captacao_liquida),
                format_money_millions(f.pl_fim),
                f"{f.cotistas_fim:,}".replace(",", "."),
                _format_rentab(f),
            )
        console.print(in_table)

    # Tabela 3: Top 5 Resgates
    top_outflow = sorted([f for f in latest_flows if f.captacao_liquida < 0], key=lambda f: f.captacao_liquida)[:5]
    if top_outflow:
        out_table = Table(title=f"\nTop 5 Maiores Resgates ({latest_week})", title_style="bold red")
        out_table.add_column("Ticker / Nome", style="cyan")
        out_table.add_column("Tipo")
        out_table.add_column("Categoria")
        out_table.add_column("Gestora")
        out_table.add_column("Captação Líquida", justify="right", style="bold red")
        out_table.add_column("PL Fim", justify="right")
        out_table.add_column("Cotistas", justify="right")
        out_table.add_column("Retorno Semanal", justify="right")

        for f in top_outflow:
            name = f.ticker or f.etf_name[:30]
            out_table.add_row(
                name,
                _KIND_LABEL.get(f.kind, f.kind),
                f.category,
                (f.manager_name or "-")[:25],
                format_money_millions(f.captacao_liquida),
                format_money_millions(f.pl_fim),
                f"{f.cotistas_fim:,}".replace(",", "."),
                _format_rentab(f),
            )
        console.print(out_table)

    _print_valorizacao_ranking(console, latest_flows, latest_week)
    _print_cotistas_ranking(console, latest_flows, latest_week)


def _print_valorizacao_ranking(
    console: Console, flows: list[EtfWeeklyFlow], week: str
) -> None:
    """Ranking de valorizacao: quanto do PL veio de mercado, e nao de captacao.

    Duas medidas lado a lado de proposito. O retorno da cota e a performance por
    unidade (comparavel entre ETFs de tamanhos diferentes); a valorizacao em reais
    e o quanto do patrimonio o mercado somou ou tirou. Um ETF pequeno pode liderar
    a primeira e nao aparecer na segunda - sao perguntas distintas.
    """
    ranked = [f for f in flows if f.rentabilidade_semana is not None]
    if not ranked:
        return
    ranked.sort(key=lambda f: f.rentabilidade_semana or 0.0, reverse=True)

    console.print()
    table = Table(
        title=f"Ranking de Valorizacao ({week}) - retorno da cota",
        title_style="bold",
    )
    table.add_column("Ticker / Nome", style="cyan")
    table.add_column("Categoria")
    table.add_column("Retorno da cota", justify="right")
    table.add_column("Valorizacao (R$)", justify="right")
    table.add_column("Captacao liquida", justify="right")
    table.add_column("Variacao de PL", justify="right")

    def row(flow: EtfWeeklyFlow) -> None:
        style = "green" if (flow.rentabilidade_semana or 0) > 0 else "red"
        table.add_row(
            (flow.ticker or flow.etf_name[:30]) + (" *" if flow.decomposicao_suspeita else ""),
            flow.category,
            f"[{style}]{flow.rentabilidade_semana:+.2f}%[/]",
            format_money_millions(flow.valorizacao),
            format_money_millions(flow.captacao_liquida),
            format_money_millions(flow.variacao_pl),
        )

    for flow in ranked[:10]:
        row(flow)
    if len(ranked) > 12:
        table.add_section()
        for flow in ranked[-3:]:
            row(flow)

    console.print(table)
    # A cota do informe e cota patrimonial ex-provento: num ETF que distribui, o
    # retorno medido aqui fica abaixo do retorno total do investidor. O informe
    # diario nao traz provento pago, entao nao ha como reincorporar sem uma
    # segunda fonte - dizer isso e mais util que publicar o numero sem ressalva.
    console.print(
        "[dim]Retorno da cota, ex-provento: o informe diario nao publica provento "
        "pago, entao um ETF que distribui aparece abaixo do retorno total.[/]"
    )
    duvidosos = [f for f in flows if f.decomposicao_suspeita]
    if duvidosos:
        console.print(
            f"[yellow]* {len(duvidosos)} de {len(flows)} ETF(s) com decomposicao "
            "suspeita:[/] a valorizacao por diferenca nao bate com a que a cota "
            "implica - semana com dia faltando na coleta, nao movimento real."
        )


def _print_cotistas_ranking(
    console: Console, flows: list[EtfWeeklyFlow], week: str
) -> None:
    """Quem ganhou e quem perdeu cotistas na semana."""
    # Registros com cotistas desmentidos pelo PL ficam FORA do ranking e do
    # saldo, como o retorno implausivel ja ficava: publicar -51 mil cotistas de um
    # erro de preenchimento e pior que nao publicar numero nenhum.
    descartados = [f for f in flows if f.cotistas_suspeito]
    moved = [f for f in flows if f.variacao_cotistas and not f.cotistas_suspeito]
    if not moved:
        return
    moved.sort(key=lambda f: f.variacao_cotistas, reverse=True)

    console.print()
    table = Table(title=f"Evolucao de Cotistas ({week})", title_style="bold")
    table.add_column("Ticker / Nome", style="cyan")
    table.add_column("Categoria")
    table.add_column("Cotistas inicio", justify="right")
    table.add_column("Cotistas fim", justify="right")
    table.add_column("Variacao", justify="right")

    def row(flow: EtfWeeklyFlow) -> None:
        style = "green" if flow.variacao_cotistas > 0 else "red"
        table.add_row(
            flow.ticker or flow.etf_name[:30],
            flow.category,
            f"{flow.cotistas_inicio:,}".replace(",", "."),
            f"{flow.cotistas_fim:,}".replace(",", "."),
            f"[{style}]{flow.variacao_cotistas:+,}[/]".replace(",", "."),
        )

    for flow in moved[:8]:
        row(flow)
    perdas = [f for f in moved if f.variacao_cotistas < 0]
    if perdas:
        table.add_section()
        for flow in perdas[-3:]:
            row(flow)

    total = sum(f.variacao_cotistas for f in moved)
    console.print(table)
    console.print(
        f"[bold]Saldo de cotistas na semana:[/] {total:+,}".replace(",", ".")
        + f" em {len(moved)} ETF(s) com movimento"
    )
    if descartados:
        console.print(
            f"[yellow]{len(descartados)} ETF(s) descartado(s) do saldo:[/] variacao "
            "de cotistas que o PL desmente (erro de preenchimento na origem)."
        )


def export_etf_csv(weekly_flows: list[EtfWeeklyFlow], output_path: Path) -> Path:
    """Exporta o histórico completo semanal de captação de ETFs para CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "CNPJ", "Tipo", "Ticker", "Nome ETF", "Categoria", "Gestora", "Semana ISO",
        "Data Fim Semana", "Captacao Bruta (RS)", "Resgates (RS)",
        "Captacao Liquida (RS)", "PL Inicio (RS)", "PL Fim (RS)",
        "Variacao PL (RS)", "Valorizacao (RS)",
        "Cotistas Inicio", "Cotistas Fim", "Variacao Cotistas",
        "Rentabilidade Semanal (%)", "Dado Suspeito", "Decomposicao Suspeita",
        "Cotistas Suspeito",
    ]

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers)
        for f in weekly_flows:
            writer.writerow([
                format_cnpj(f.cnpj),
                _KIND_LABEL.get(f.kind, f.kind),
                f.ticker or "",
                f.etf_name,
                f.category,
                f.manager_name or "",
                f.week_key,
                f.week_end_date.strftime("%Y-%m-%d"),
                f.captacao_bruta,
                f.resgates,
                f.captacao_liquida,
                f.pl_inicio,
                f.pl_fim,
                f.variacao_pl,
                f.valorizacao,
                f.cotistas_inicio,
                f.cotistas_fim,
                f.variacao_cotistas,
                f.rentabilidade_semana if f.rentabilidade_semana is not None else "",
                "sim" if f.data_suspeita else "",
                "sim" if f.decomposicao_suspeita else "",
                "sim" if f.cotistas_suspeito else "",
            ])

    return output_path


def export_etf_excel(
    weekly_flows: list[EtfWeeklyFlow],
    category_summaries: list[CategoryWeeklySummary],
    catalog: dict[str, EtfMetadata],
    output_path: Path,
    pl_series: list[EtfMonthlyPl] | None = None,
) -> Path:
    """Exporta a planilha Excel completa com matriz semanal por ETF e por categoria."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # --- Aba 1: Resumo Categorias x Semanas
    ws_cat = wb.active
    ws_cat.title = "Resumo Categorias"

    weeks = sorted({f.week_key for f in weekly_flows})
    categories = sorted({f.category for f in weekly_flows})

    ws_cat.cell(row=1, column=1, value="Captação Líquida de ETFs por Categoria (Semana a Semana)").font = _TITLE_FONT
    ws_cat.cell(row=2, column=1, value=f"Gerado em {datetime.now():%d/%m/%Y %H:%M}").font = _NOTE_FONT

    headers_cat = ["Categoria", *weeks, "Total Acumulado (R$)"]
    for col_idx, h in enumerate(headers_cat, start=1):
        ws_cat.cell(row=4, column=col_idx, value=h)
        ws_cat.column_dimensions[get_column_letter(col_idx)].width = 24 if col_idx == 1 else 18

    # Estilizar cabeçalho
    for col_idx in range(1, len(headers_cat) + 1):
        cell = ws_cat.cell(row=4, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center", horizontal="center" if col_idx > 1 else "left")

    cat_map: dict[tuple[str, str], float] = {(c.category, c.week_key): c.captacao_liquida for c in category_summaries}

    row_idx = 5
    for cat in categories:
        ws_cat.cell(row=row_idx, column=1, value=cat).font = _BODY_FONT
        for w_idx, w in enumerate(weeks, start=2):
            val = cat_map.get((cat, w), 0.0)
            cell = ws_cat.cell(row=row_idx, column=w_idx, value=val)
            cell.font = _BODY_FONT
            cell.number_format = _MONEY
            if val > 0:
                cell.fill = _GREEN_FILL
            elif val < 0:
                cell.fill = _RED_FILL

        # Fórmula de Total Acumulado por Linha
        col_start = get_column_letter(2)
        col_end = get_column_letter(len(weeks) + 1)
        tot_cell = ws_cat.cell(row=row_idx, column=len(weeks) + 2, value=f"=SUM({col_start}{row_idx}:{col_end}{row_idx})")
        tot_cell.font = Font(name=FONT, size=10, bold=True)
        tot_cell.number_format = _MONEY
        row_idx += 1

    # --- Aba 2: Matriz Semanal por ETF
    ws_etf = wb.create_sheet("Matriz ETFs")
    ws_etf.cell(row=1, column=1, value="Captação Líquida por ETF (Semana a Semana)").font = _TITLE_FONT

    headers_etf = ["Ticker", "Tipo", "Nome", "Categoria", "Gestora", *weeks, "Total Acumulado (R$)"]
    _widths_etf = {1: 14, 2: 10, 3: 36}
    for col_idx, h in enumerate(headers_etf, start=1):
        ws_etf.cell(row=3, column=col_idx, value=h)
        ws_etf.column_dimensions[get_column_letter(col_idx)].width = _widths_etf.get(col_idx, 18)

    for col_idx in range(1, len(headers_etf) + 1):
        cell = ws_etf.cell(row=3, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")

    etf_cnpjs = sorted(
        {f.cnpj for f in weekly_flows},
        key=lambda c: catalog[c].ticker or catalog[c].name if c in catalog else c,
    )

    etf_week_map: dict[tuple[str, str], float] = {(f.cnpj, f.week_key): f.captacao_liquida for f in weekly_flows}
    etf_kind_map: dict[str, str] = {f.cnpj: f.kind for f in weekly_flows}

    row_idx = 4
    for cnpj in etf_cnpjs:
        meta = catalog.get(cnpj)
        ticker = meta.ticker if meta else "-"
        name = meta.name if meta else cnpj
        category = meta.category if meta else "Outros"
        manager = meta.manager_name if meta else "-"
        kind = _KIND_LABEL.get(etf_kind_map.get(cnpj, "etf"), "ETF")

        ws_etf.cell(row=row_idx, column=1, value=ticker).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=2, value=kind).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=3, value=name).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=4, value=category).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=5, value=manager).font = _BODY_FONT

        for w_idx, w in enumerate(weeks, start=6):
            val = etf_week_map.get((cnpj, w), 0.0)
            cell = ws_etf.cell(row=row_idx, column=w_idx, value=val)
            cell.font = _BODY_FONT
            cell.number_format = _MONEY
            if val > 0:
                cell.fill = _GREEN_FILL
            elif val < 0:
                cell.fill = _RED_FILL

        col_start = get_column_letter(6)
        col_end = get_column_letter(len(weeks) + 5)
        tot_cell = ws_etf.cell(row=row_idx, column=len(weeks) + 6, value=f"=SUM({col_start}{row_idx}:{col_end}{row_idx})")
        tot_cell.font = Font(name=FONT, size=10, bold=True)
        tot_cell.number_format = _MONEY
        row_idx += 1

    ws_etf.freeze_panes = "F4"

    # --- Aba 3: Detalhamento Completo
    ws_flat = wb.create_sheet("Detalhamento Completo")
    headers_flat = [
        "CNPJ", "Tipo", "Ticker", "Nome ETF", "Categoria", "Gestora", "Semana ISO",
        "Data Fim", "Captação Bruta (R$)", "Resgates (R$)", "Captação Líquida (R$)",
        "PL Fim (R$)", "Variação PL (R$)", "Valorização (R$)",
        "Cotistas Fim", "Variação Cotistas", "Rentabilidade Semanal (%)",
        "Dado Suspeito", "Decomposição Suspeita", "Cotistas Suspeito",
    ]
    widths_flat = [18, 10, 12, 38, 16, 26, 12, 14, 18, 18, 20, 18, 18, 18, 14, 16, 20, 14, 20, 18]

    for col_idx, (h, w) in enumerate(zip(headers_flat, widths_flat), start=1):
        ws_flat.cell(row=1, column=col_idx, value=h)
        ws_flat.column_dimensions[get_column_letter(col_idx)].width = w

    for col_idx in range(1, len(headers_flat) + 1):
        cell = ws_flat.cell(row=1, column=col_idx)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")

    row_idx = 2
    for f in weekly_flows:
        values = [
            format_cnpj(f.cnpj),
            _KIND_LABEL.get(f.kind, f.kind),
            f.ticker or "",
            f.etf_name,
            f.category,
            f.manager_name or "",
            f.week_key,
            f.week_end_date.strftime("%d/%m/%Y"),
            f.captacao_bruta,
            f.resgates,
            f.captacao_liquida,
            f.pl_fim,
            f.variacao_pl,
            f.valorizacao,
            f.cotistas_fim,
            f.variacao_cotistas,
            (f.rentabilidade_semana / 100.0) if f.rentabilidade_semana is not None else None,
            "sim" if f.data_suspeita else "",
            "sim" if f.decomposicao_suspeita else "",
            "sim" if f.cotistas_suspeito else "",
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws_flat.cell(row=row_idx, column=col_idx, value=val)
            cell.font = _BODY_FONT
            cell.border = _BORDER

        # 9-14 sao reais (captacao bruta, resgates, liquida, PL fim, variacao de
        # PL, valorizacao); 15-16 contagem de cotistas; 17 percentual.
        for money_col in range(9, 15):
            ws_flat.cell(row=row_idx, column=money_col).number_format = _MONEY
        ws_flat.cell(row=row_idx, column=15).number_format = _INT
        ws_flat.cell(row=row_idx, column=16).number_format = _INT
        ws_flat.cell(row=row_idx, column=17).number_format = _PCT
        row_idx += 1

    ws_flat.freeze_panes = "A2"
    if row_idx > 2:
        ws_flat.auto_filter.ref = f"A1:T{row_idx - 1}"

    _write_catalog_sheet(wb, catalog)
    if pl_series:
        _write_pl_sheet(wb, pl_series)

    wb.save(output_path)
    return output_path


def _write_catalog_sheet(wb: Workbook, catalog: dict[str, EtfMetadata]) -> None:
    """Aba "Catálogo FIIM": todo ETF cadastrado na CVM, tenha fluxo ou não.

    É a resposta direta a "o monitor traz todos os ETFs do mercado, incluindo
    os de renda fixa?" - sem depender do INF_DIARIO, que não cobre FIIM (ver
    docstring de market/etf_flow.py). Feeders entram numa seção separada, no
    fim da aba: são relevantes para a prateleira, mas não são o ETF em si.
    """
    ws = wb.create_sheet("Catálogo FIIM")
    ws.cell(row=1, column=1, value="Catálogo de ETFs (FIIM) registrados na CVM").font = _TITLE_FONT
    ws.cell(row=2, column=1, value=f"Gerado em {datetime.now():%d/%m/%Y %H:%M}").font = _NOTE_FONT

    headers = [
        "CNPJ", "Ticker", "Nome", "Categoria", "Benchmark", "Gestora",
        "Situação", "PL do Cadastro (R$)",
    ]
    widths = [18, 12, 42, 16, 20, 30, 20, 20]
    header_row = 4
    for col_idx, (h, w) in enumerate(zip(headers, widths), start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=h)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        cell.alignment = Alignment(vertical="center")
        ws.column_dimensions[get_column_letter(col_idx)].width = w

    def write_rows(items: list[EtfMetadata], start_row: int) -> int:
        row_idx = start_row
        for meta in sorted(items, key=lambda m: (m.category, m.ticker or m.name)):
            values = [
                format_cnpj(meta.cnpj),
                meta.ticker or "",
                meta.name,
                meta.category,
                meta.benchmark or "",
                meta.manager_name or "",
                meta.situation or "",
                meta.net_worth,
            ]
            for col_idx, val in enumerate(values, start=1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.font = _BODY_FONT
                cell.border = _BORDER
            ws.cell(row=row_idx, column=8).number_format = _MONEY
            row_idx += 1
        return row_idx

    etfs = [m for m in catalog.values() if m.is_etf]
    feeders = [m for m in catalog.values() if not m.is_etf]

    row_idx = write_rows(etfs, header_row + 1)
    ws.freeze_panes = f"A{header_row + 1}"
    if row_idx > header_row + 1:
        ws.auto_filter.ref = f"A{header_row}:H{row_idx - 1}"

    if feeders:
        row_idx += 2
        ws.cell(
            row=row_idx, column=1,
            value="Feeders (FIF que apenas replicam um ETF - não somam com o ETF acima)",
        ).font = _NOTE_FONT
        row_idx += 1
        feeder_header_row = row_idx
        for col_idx, h in enumerate(headers, start=1):
            cell = ws.cell(row=feeder_header_row, column=col_idx, value=h)
            cell.fill = _HEADER_FILL
            cell.font = _HEADER_FONT
        write_rows(feeders, feeder_header_row + 1)


def print_etf_pl(
    series: list[EtfMonthlyPl],
    coverage: PlCoverage,
    missing: list[str] | None = None,
) -> None:
    """Exibe o PL mensal dos ETFs vindo do balancete de FIE.

    Fica separado de print_etf_summary de proposito: sao fontes com cadencia e
    semantica diferentes (semanal/captacao contra mensal/PL) e juntar as duas
    numa tabela convidaria a somar numeros que nao se somam.
    """
    console = Console()

    if not series:
        console.print(
            "[yellow]Balancete de FIE sem nenhum ETF do catalogo.[/] "
            "Nenhuma competencia disponivel no periodo pedido."
        )
        return

    latest = coverage.competencia
    current = [s for s in series if s.competencia == latest]
    comparable = [s for s in current if s.variacao_pl is not None]
    comparable.sort(key=lambda s: -abs(s.variacao_pl or 0))

    label = f"{latest[4:]}/{latest[:4]}" if latest else "?"
    table = Table(
        title=f"PL dos ETFs no balancete de FIE - competencia {label}",
        title_style="bold",
    )
    table.add_column("ETF")
    table.add_column("Categoria")
    table.add_column("PL", justify="right")
    table.add_column("Variacao no mes", justify="right")
    table.add_column("%", justify="right")

    for item in comparable[:15]:
        variacao = item.variacao_pl or 0.0
        style = "green" if variacao > 0 else "red" if variacao < 0 else ""
        pct = f"{item.variacao_pct:+.1f}%" if item.variacao_pct is not None else "-"
        table.add_row(
            (item.ticker and f"{item.ticker} - {item.etf_name}" or item.etf_name)[:44],
            item.category[:18],
            format_money_millions(item.pl),
            f"[{style}]{format_money_millions(variacao)}[/]" if style else format_money_millions(variacao),
            f"[{style}]{pct}[/]" if style else pct,
        )

    console.print()
    console.print(table)
    console.print(
        f"[bold]Cobertura:[/] {coverage.etfs_com_pl} de {coverage.etfs_no_catalogo} "
        f"ETFs do catalogo ({coverage.coverage_ratio:.0%}), "
        f"PL somado {format_money_millions(coverage.pl_total)}"
    )
    # A ressalva sai junto do numero, sempre: sem ela a variacao de PL seria lida
    # como captacao - e ela inclui marcacao a mercado. Ver market/fie_balancete.py.
    console.print(
        "[dim]Variacao de PL nao e captacao liquida: inclui valorizacao. O "
        "balancete nao traz cotas em circulacao, que e o que separaria as duas.[/]"
    )
    if coverage.identidades_suspeitas:
        console.print(
            f"[yellow]{coverage.identidades_suspeitas} ETF(s) com identidade "
            "contabil fora de linha[/] (PL != capital de cotas + resultado) - "
            "sinal de que o plano de contas mudou; confira market/fie_balancete.py."
        )
    if missing:
        console.print(
            f"[dim]Competencias sem balancete publicado: {', '.join(missing)} "
            "(a CVM publica com defasagem).[/]"
        )


def _write_pl_sheet(wb: Workbook, series: list[EtfMonthlyPl]) -> None:
    """Aba "PL mensal (FIE)": a serie de PL por ETF e competencia."""
    ws = wb.create_sheet("PL mensal (FIE)")

    ws.cell(row=1, column=1, value="PL mensal dos ETFs - balancete de Fundos Estruturados (CVM)").font = _TITLE_FONT
    ws.cell(
        row=2, column=1,
        value=(
            "Variacao de PL NAO e captacao liquida: inclui valorizacao. "
            "O balancete nao publica cotas em circulacao."
        ),
    ).font = _NOTE_FONT

    headers = [
        "Competencia", "Ticker", "ETF", "CNPJ", "Categoria", "Gestora",
        "PL (R$)", "PL anterior (R$)", "Variacao (R$)", "Variacao (%)",
    ]
    header_row = 4
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=header)
        cell.fill = _HEADER_FILL
        cell.font = _HEADER_FONT
        ws.column_dimensions[get_column_letter(col_idx)].width = 44 if col_idx == 3 else 18

    row_idx = header_row + 1
    for item in series:
        values = [
            item.competencia,
            item.ticker or "",
            item.etf_name,
            format_cnpj(item.cnpj),
            item.category,
            item.manager_name or "",
            item.pl,
            item.pl_anterior,
            item.variacao_pl,
            item.variacao_pct,
        ]
        for col_idx, value in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = _BODY_FONT
            if col_idx in (7, 8, 9):
                cell.number_format = _MONEY
            elif col_idx == 10 and value is not None:
                cell.number_format = "0.0"
            # Reatribuir cell.fill a si mesmo levanta TypeError (StyleProxy nao
            # e hashable), entao a cor so e tocada quando ha sinal a mostrar.
            if col_idx == 9 and isinstance(value, (int, float)) and value:
                cell.fill = _GREEN_FILL if value > 0 else _RED_FILL
        row_idx += 1

    ws.freeze_panes = f"A{header_row + 1}"
    if row_idx > header_row + 1:
        ws.auto_filter.ref = f"A{header_row}:J{row_idx - 1}"
