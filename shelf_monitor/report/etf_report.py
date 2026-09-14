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

from shelf_monitor.config import Config
from shelf_monitor.market.etf import EtfMetadata
from shelf_monitor.market.etf_flow import CategoryWeeklySummary, EtfWeeklyFlow
from shelf_monitor.normalize import format_cnpj

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
) -> None:
    """Exibe no terminal a visão resumida da captação de ETFs."""
    console = Console()
    if not weekly_flows:
        console.print("[yellow]Nenhum dado de captação semanal encontrado.[/]")
        return

    # Descobrir a última semana disponível
    latest_week = max(f.week_key for f in weekly_flows)
    latest_flows = [f for f in weekly_flows if f.week_key == latest_week]
    latest_cats = [c for c in category_summaries if c.week_key == latest_week]

    console.print()
    console.print(f"[bold cyan]=== MONITORAMENTO DE CAPTAÇÃO DE ETFs (Semana {latest_week}) ===[/]")
    console.print(f"[dim]Total de ETFs catalogados na CVM: {len(catalog)} | ETFs com movimento na semana: {len(latest_flows)}[/]\n")

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
        in_table.add_column("Categoria")
        in_table.add_column("Gestora")
        in_table.add_column("Captação Líquida", justify="right", style="bold green")
        in_table.add_column("PL Fim", justify="right")
        in_table.add_column("Cotistas", justify="right")
        in_table.add_column("Retorno Semanal", justify="right")

        for f in top_inflow:
            name = f.ticker or f.etf_name[:30]
            rentab = f"{f.rentabilidade_semana:+.2f}%" if f.rentabilidade_semana is not None else "-"
            in_table.add_row(
                name,
                f.category,
                (f.manager_name or "-")[:25],
                format_money_millions(f.captacao_liquida),
                format_money_millions(f.pl_fim),
                f"{f.cotistas_fim:,}".replace(",", "."),
                rentab,
            )
        console.print(in_table)

    # Tabela 3: Top 5 Resgates
    top_outflow = sorted([f for f in latest_flows if f.captacao_liquida < 0], key=lambda f: f.captacao_liquida)[:5]
    if top_outflow:
        out_table = Table(title=f"\nTop 5 Maiores Resgates ({latest_week})", title_style="bold red")
        out_table.add_column("Ticker / Nome", style="cyan")
        out_table.add_column("Categoria")
        out_table.add_column("Gestora")
        out_table.add_column("Captação Líquida", justify="right", style="bold red")
        out_table.add_column("PL Fim", justify="right")
        out_table.add_column("Cotistas", justify="right")
        out_table.add_column("Retorno Semanal", justify="right")

        for f in top_outflow:
            name = f.ticker or f.etf_name[:30]
            rentab = f"{f.rentabilidade_semana:+.2f}%" if f.rentabilidade_semana is not None else "-"
            out_table.add_row(
                name,
                f.category,
                (f.manager_name or "-")[:25],
                format_money_millions(f.captacao_liquida),
                format_money_millions(f.pl_fim),
                f"{f.cotistas_fim:,}".replace(",", "."),
                rentab,
            )
        console.print(out_table)


def export_etf_csv(weekly_flows: list[EtfWeeklyFlow], output_path: Path) -> Path:
    """Exporta o histórico completo semanal de captação de ETFs para CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    headers = [
        "CNPJ", "Ticker", "Nome ETF", "Categoria", "Gestora", "Semana ISO",
        "Data Fim Semana", "Captacao Bruta (RS)", "Resgates (RS)",
        "Captacao Liquida (RS)", "PL Inicio (RS)", "PL Fim (RS)",
        "Cotistas Inicio", "Cotistas Fim", "Variacao Cotistas", "Rentabilidade Semanal (%)",
    ]

    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(headers)
        for f in weekly_flows:
            writer.writerow([
                format_cnpj(f.cnpj),
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
                f.cotistas_inicio,
                f.cotistas_fim,
                f.variacao_cotistas,
                f.rentabilidade_semana if f.rentabilidade_semana is not None else "",
            ])

    return output_path


def export_etf_excel(
    weekly_flows: list[EtfWeeklyFlow],
    category_summaries: list[CategoryWeeklySummary],
    catalog: dict[str, EtfMetadata],
    output_path: Path,
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

    headers_etf = ["Ticker", "Nome do ETF", "Categoria", "Gestora", *weeks, "Total Acumulado (R$)"]
    for col_idx, h in enumerate(headers_etf, start=1):
        ws_etf.cell(row=3, column=col_idx, value=h)
        ws_etf.column_dimensions[get_column_letter(col_idx)].width = (
            14 if col_idx == 1 else (36 if col_idx == 2 else 18)
        )

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

    row_idx = 4
    for cnpj in etf_cnpjs:
        meta = catalog.get(cnpj)
        ticker = meta.ticker if meta else "-"
        name = meta.name if meta else cnpj
        category = meta.category if meta else "Outros"
        manager = meta.manager_name if meta else "-"

        ws_etf.cell(row=row_idx, column=1, value=ticker).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=2, value=name).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=3, value=category).font = _BODY_FONT
        ws_etf.cell(row=row_idx, column=4, value=manager).font = _BODY_FONT

        for w_idx, w in enumerate(weeks, start=5):
            val = etf_week_map.get((cnpj, w), 0.0)
            cell = ws_etf.cell(row=row_idx, column=w_idx, value=val)
            cell.font = _BODY_FONT
            cell.number_format = _MONEY
            if val > 0:
                cell.fill = _GREEN_FILL
            elif val < 0:
                cell.fill = _RED_FILL

        col_start = get_column_letter(5)
        col_end = get_column_letter(len(weeks) + 4)
        tot_cell = ws_etf.cell(row=row_idx, column=len(weeks) + 5, value=f"=SUM({col_start}{row_idx}:{col_end}{row_idx})")
        tot_cell.font = Font(name=FONT, size=10, bold=True)
        tot_cell.number_format = _MONEY
        row_idx += 1

    ws_etf.freeze_panes = "E4"

    # --- Aba 3: Detalhamento Completo
    ws_flat = wb.create_sheet("Detalhamento Completo")
    headers_flat = [
        "CNPJ", "Ticker", "Nome ETF", "Categoria", "Gestora", "Semana ISO",
        "Data Fim", "Captação Bruta (R$)", "Resgates (R$)", "Captação Líquida (R$)",
        "PL Fim (R$)", "Cotistas Fim", "Variação Cotistas", "Rentabilidade Semanal (%)",
    ]
    widths_flat = [18, 12, 38, 16, 26, 12, 14, 18, 18, 20, 18, 14, 16, 20]

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
            f.cotistas_fim,
            f.variacao_cotistas,
            (f.rentabilidade_semana / 100.0) if f.rentabilidade_semana is not None else None,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws_flat.cell(row=row_idx, column=col_idx, value=val)
            cell.font = _BODY_FONT
            cell.border = _BORDER

        ws_flat.cell(row=row_idx, column=8).number_format = _MONEY
        ws_flat.cell(row=row_idx, column=9).number_format = _MONEY
        ws_flat.cell(row=row_idx, column=10).number_format = _MONEY
        ws_flat.cell(row=row_idx, column=11).number_format = _MONEY
        ws_flat.cell(row=row_idx, column=12).number_format = _INT
        ws_flat.cell(row=row_idx, column=13).number_format = _INT
        ws_flat.cell(row=row_idx, column=14).number_format = _PCT
        row_idx += 1

    ws_flat.freeze_panes = "A2"
    if row_idx > 2:
        ws_flat.auto_filter.ref = f"A1:N{row_idx - 1}"

    wb.save(output_path)
    return output_path
