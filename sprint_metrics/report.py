from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from sprint_metrics.models import (
    AlertSeverity,
    HealthMetrics,
    SprintReport,
    StruggleAlert,
    TaskAnalysis,
    VelocityAggregation,
)

console = Console()

SEVERITY_COLORS = {
    AlertSeverity.LOW: "blue",
    AlertSeverity.MEDIUM: "yellow",
    AlertSeverity.HIGH: "red",
    AlertSeverity.CRITICAL: "bold red",
}

SEVERITY_ICONS = {
    AlertSeverity.LOW: "[i]",
    AlertSeverity.MEDIUM: "[!]",
    AlertSeverity.HIGH: "[!!]",
    AlertSeverity.CRITICAL: "[!!!]",
}


def render_report(report: SprintReport) -> None:
    """Render the full sprint report to the terminal."""
    sprint = report.sprint
    console.print()
    console.rule(f"[bold cyan]Sprint: {sprint.name}[/]")

    if sprint.start_date and sprint.end_date:
        console.print(
            f"  Periodo: {sprint.start_date.strftime('%d/%m/%Y')} - "
            f"{sprint.end_date.strftime('%d/%m/%Y')}"
        )
    console.print()

    _render_struggle_alerts(report.struggle_alerts)
    _render_task_analyses(report.task_analyses)
    _render_velocity(report.velocity_by_dimension)
    _render_health(report.health)


def _render_struggle_alerts(alerts: list[StruggleAlert]) -> None:
    """Render struggle detection alerts."""
    if not alerts:
        console.print(
            Panel(
                "[green]Nenhum alerta de dificuldade detectado[/]",
                title="Alertas de Dificuldade",
            )
        )
        return

    table = Table(title="Alertas de Dificuldade", show_lines=True)
    table.add_column("Sev.", width=5, justify="center")
    table.add_column("Engenheiro", min_width=15)
    table.add_column("WI", width=7, justify="right")
    table.add_column("Tarefa", min_width=20)
    table.add_column("Motivo", min_width=15)
    table.add_column("Detalhes", min_width=25)

    # Sort by severity (critical first)
    severity_order = {
        AlertSeverity.CRITICAL: 0,
        AlertSeverity.HIGH: 1,
        AlertSeverity.MEDIUM: 2,
        AlertSeverity.LOW: 3,
    }
    sorted_alerts = sorted(alerts, key=lambda a: severity_order.get(a.severity, 9))

    for alert in sorted_alerts:
        color = SEVERITY_COLORS[alert.severity]
        icon = SEVERITY_ICONS[alert.severity]
        table.add_row(
            Text(icon, style=color),
            alert.engineer,
            str(alert.work_item_id),
            alert.work_item_title[:40],
            alert.reason.value,
            alert.details,
        )

    console.print(table)
    console.print()


def _render_task_analyses(analyses: list[TaskAnalysis]) -> None:
    """Render task analysis summary for items with alerts."""
    items_with_alerts = [a for a in analyses if a.alerts]
    if not items_with_alerts:
        return

    table = Table(title="Analise de Tarefas com Alertas", show_lines=True)
    table.add_column("WI", width=7, justify="right")
    table.add_column("Tarefa", min_width=20)
    table.add_column("Responsavel", min_width=12)
    table.add_column("Estado", width=12)
    table.add_column("Dias Prog.", width=10, justify="right")
    table.add_column("Bounces", width=8, justify="center")
    table.add_column("Re-attr.", width=8, justify="center")
    table.add_column("Alertas", width=8, justify="center")

    for a in items_with_alerts[:20]:  # Top 20
        alert_color = "red" if len(a.alerts) >= 2 else "yellow"
        table.add_row(
            str(a.work_item_id),
            a.title[:35],
            a.assigned_to,
            a.state,
            f"{a.days_in_progress:.1f}",
            str(a.bounceback_count) if a.bounceback_count else "-",
            str(a.reassignment_count) if a.reassignment_count else "-",
            Text(str(len(a.alerts)), style=alert_color),
        )

    console.print(table)
    console.print()


def _render_velocity(
    velocity_by_dim: dict[str, list[VelocityAggregation]],
) -> None:
    """Render velocity aggregations by dimension."""
    if not velocity_by_dim:
        console.print(
            Panel(
                "[yellow]Sem dados de velocidade historica[/]",
                title="Velocidade Historica",
            )
        )
        return

    for dim_label, aggregations in velocity_by_dim.items():
        if not aggregations:
            continue

        table = Table(title=f"Velocidade: {dim_label}", show_lines=True)
        table.add_column(aggregations[0].dimension_name, min_width=15)
        table.add_column("Hrs/Ponto (med)", width=15, justify="right")
        table.add_column("Hrs/Ponto (avg)", width=15, justify="right")
        table.add_column("Total Horas", width=12, justify="right")
        table.add_column("Total Pontos", width=12, justify="right")
        table.add_column("Amostras", width=9, justify="center")

        for agg in aggregations:
            hpp_med = f"{agg.median_hours_per_point:.2f}" if agg.median_hours_per_point else "-"
            hpp_avg = f"{agg.avg_hours_per_point:.2f}" if agg.avg_hours_per_point else "-"

            table.add_row(
                agg.dimension_value,
                hpp_med,
                hpp_avg,
                f"{agg.total_hours:.1f}",
                f"{agg.total_points:.1f}",
                str(agg.sample_count),
            )

        console.print(table)
        console.print()


def _render_health(health: HealthMetrics) -> None:
    """Render sprint health indicators."""
    console.rule("[bold]Saude da Sprint")
    console.print()

    # WIP per engineer
    if health.wip_per_engineer:
        table = Table(title="WIP por Engenheiro (itens In Progress)")
        table.add_column("Engenheiro", min_width=15)
        table.add_column("Itens Ativos", width=12, justify="center")

        for eng, count in sorted(
            health.wip_per_engineer.items(), key=lambda x: x[1], reverse=True
        ):
            color = "red" if count > 3 else ("yellow" if count > 2 else "green")
            table.add_row(eng, Text(str(count), style=color))

        console.print(table)
        console.print()

    # Blocked items
    if health.blocked_items:
        table = Table(title="Itens Bloqueados (aging)")
        table.add_column("WI", width=7, justify="right")
        table.add_column("Tarefa", min_width=20)
        table.add_column("Responsavel", min_width=12)
        table.add_column("Dias Bloqueado", width=14, justify="right")

        for item in health.blocked_items:
            days = item["days_blocked"]
            color = "red" if days > 3 else ("yellow" if days > 1 else "white")
            table.add_row(
                str(item["id"]),
                item["title"][:35],
                item["assigned_to"],
                Text(str(days), style=color),
            )

        console.print(table)
        console.print()

    # Carry-over
    carry_color = (
        "red" if health.carry_over_rate > 20 else ("yellow" if health.carry_over_rate > 10 else "green")
    )
    console.print(
        f"  Carry-over rate: [{carry_color}]{health.carry_over_rate:.1f}%[/] "
        f"({len(health.carry_over_items)} itens da sprint anterior)"
    )

    # Burndown deviation
    if health.ideal_remaining > 0 or health.actual_remaining > 0:
        dev = health.burndown_deviation
        dev_color = "red" if dev > 15 else ("yellow" if dev > 5 else "green")
        console.print(
            f"  Burndown: ideal={health.ideal_remaining}h, "
            f"real={health.actual_remaining}h, "
            f"desvio=[{dev_color}]{dev:+.1f}%[/]"
        )

    # Task switching
    if health.task_switching:
        table = Table(title="Task Switching (dias com >1 tarefa tocada)")
        table.add_column("Engenheiro", min_width=15)
        table.add_column("Dias Multi-tarefa", width=18, justify="center")

        for eng, days in sorted(
            health.task_switching.items(), key=lambda x: x[1], reverse=True
        ):
            table.add_row(eng, str(days))

        console.print(table)

    console.print()
