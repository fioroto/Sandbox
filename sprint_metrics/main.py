"""
Sprint Metrics CLI - Avaliacao de andamento de sprint usando Azure DevOps SDK.

Uso:
    python -m sprint_metrics.main
    python -m sprint_metrics.main --sprint current
    python -m sprint_metrics.main --sprint previous --history 6
"""
from __future__ import annotations

import argparse
import sys

from rich.console import Console

from sprint_metrics.client import AzureDevOpsClient
from sprint_metrics.collectors.iterations import (
    get_current_iteration,
    get_recent_iterations,
)
from sprint_metrics.collectors.revisions import get_batch_updates
from sprint_metrics.collectors.work_items import (
    get_sprint_work_items,
)
from sprint_metrics.config import Config
from sprint_metrics.metrics.health import compute_health_metrics
from sprint_metrics.metrics.struggle import analyze_sprint_struggles
from sprint_metrics.metrics.velocity import (
    build_velocity_records,
    compute_velocity_by_dimensions,
)
from sprint_metrics.models import SprintReport
from sprint_metrics.report import render_report

console = Console()


def run_current_sprint_analysis(
    client: AzureDevOpsClient, history_count: int
) -> None:
    """Analyze the current sprint with historical context."""
    console.print("[bold]Buscando iteracao atual...[/]")
    current = get_current_iteration(client)
    if not current:
        console.print("[red]Nenhuma sprint atual encontrada para o time configurado.[/]")
        return

    console.print(f"  Sprint atual: [cyan]{current.name}[/]")

    # Fetch current sprint work items
    console.print("[bold]Coletando work items da sprint atual...[/]")
    current_items = get_sprint_work_items(client, current)
    console.print(f"  {len(current_items)} work items encontrados")

    if not current_items:
        console.print("[yellow]Nenhum work item na sprint atual.[/]")
        return

    # Fetch updates for struggle detection
    console.print("[bold]Coletando historico de updates...[/]")
    work_item_ids = [wi.id for wi in current_items]
    updates_map = get_batch_updates(client, work_item_ids)
    console.print(f"  Updates coletados para {len(updates_map)} work items")

    # Struggle detection
    console.print("[bold]Analisando indicadores de dificuldade...[/]")
    task_analyses = analyze_sprint_struggles(current_items, updates_map, current)
    all_alerts = [alert for ta in task_analyses for alert in ta.alerts]

    # Historical velocity
    console.print(
        f"[bold]Coletando historico de velocidade ({history_count} sprints)...[/]"
    )
    past_iterations = get_recent_iterations(client, count=history_count)
    all_velocity_records = []

    for past_it in past_iterations:
        past_items = get_sprint_work_items(client, past_it)
        if not past_items:
            continue
        past_ids = [wi.id for wi in past_items]
        past_updates = get_batch_updates(client, past_ids)
        records = build_velocity_records(past_items, past_updates, past_it)
        all_velocity_records.extend(records)
        console.print(f"  {past_it.name}: {len(records)} itens concluidos")

    velocity_dims = compute_velocity_by_dimensions(all_velocity_records)

    # Health metrics
    console.print("[bold]Calculando indicadores de saude...[/]")
    previous_items = []
    if past_iterations:
        previous_items = get_sprint_work_items(client, past_iterations[0])

    health = compute_health_metrics(
        current_items, previous_items, updates_map, current
    )

    # Build and render report
    report = SprintReport(
        sprint=current,
        struggle_alerts=all_alerts,
        task_analyses=task_analyses,
        velocity_by_dimension=velocity_dims,
        health=health,
    )

    render_report(report)


def run_previous_sprint_analysis(
    client: AzureDevOpsClient, history_count: int
) -> None:
    """Analyze the most recent completed sprint."""
    console.print("[bold]Buscando sprints anteriores...[/]")
    past = get_recent_iterations(client, count=history_count)
    if not past:
        console.print("[red]Nenhuma sprint anterior encontrada.[/]")
        return

    target = past[0]
    console.print(f"  Sprint analisada: [cyan]{target.name}[/]")

    items = get_sprint_work_items(client, target)
    console.print(f"  {len(items)} work items encontrados")

    if not items:
        console.print("[yellow]Nenhum work item nesta sprint.[/]")
        return

    work_item_ids = [wi.id for wi in items]
    updates_map = get_batch_updates(client, work_item_ids)

    task_analyses = analyze_sprint_struggles(items, updates_map, target)
    all_alerts = [alert for ta in task_analyses for alert in ta.alerts]

    all_velocity_records = []
    for past_it in past:
        past_items = get_sprint_work_items(client, past_it)
        if not past_items:
            continue
        past_ids = [wi.id for wi in past_items]
        past_updates = get_batch_updates(client, past_ids)
        records = build_velocity_records(past_items, past_updates, past_it)
        all_velocity_records.extend(records)

    velocity_dims = compute_velocity_by_dimensions(all_velocity_records)

    previous_items = []
    if len(past) > 1:
        previous_items = get_sprint_work_items(client, past[1])

    health = compute_health_metrics(items, previous_items, updates_map, target)

    report = SprintReport(
        sprint=target,
        struggle_alerts=all_alerts,
        task_analyses=task_analyses,
        velocity_by_dimension=velocity_dims,
        health=health,
    )

    render_report(report)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Sprint Metrics - Avaliacao de andamento de sprint (Azure DevOps)"
    )
    parser.add_argument(
        "--sprint",
        choices=["current", "previous"],
        default="current",
        help="Sprint a analisar (default: current)",
    )
    parser.add_argument(
        "--history",
        type=int,
        default=6,
        help="Quantidade de sprints anteriores para historico de velocidade (default: 6)",
    )
    parser.add_argument("--org-url", help="Azure DevOps organization URL")
    parser.add_argument("--pat", help="Personal Access Token")
    parser.add_argument("--project", help="Project name")
    parser.add_argument("--team", help="Team name")

    args = parser.parse_args()

    config = Config(
        org_url=args.org_url,
        pat=args.pat,
        project=args.project,
        team=args.team,
        history_sprints=args.history,
    )

    errors = config.validate()
    if errors:
        console.print("[red]Erros de configuracao:[/]")
        for err in errors:
            console.print(f"  - {err}")
        console.print()
        console.print("Configure via variaveis de ambiente (.env) ou argumentos CLI:")
        console.print("  AZURE_DEVOPS_ORG_URL, AZURE_DEVOPS_PAT, AZURE_DEVOPS_PROJECT, AZURE_DEVOPS_TEAM")
        sys.exit(1)

    client = AzureDevOpsClient(config)

    if args.sprint == "current":
        run_current_sprint_analysis(client, args.history)
    else:
        run_previous_sprint_analysis(client, args.history)


if __name__ == "__main__":
    main()
