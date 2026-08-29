"""Monitor de prateleiras de fundos - CLI.

Uso:
    python -m shelf_monitor run                    # coleta tudo, diff, digest e dashboard
    python -m shelf_monitor collect --platform btg
    python -m shelf_monitor cvm                    # so o radar CVM
    python -m shelf_monitor diff
    python -m shelf_monitor report                 # re-renderiza a partir dos snapshots
    python -m shelf_monitor doctor                 # testa acesso as fontes
    python -m shelf_monitor watchlist-discover "BTG PACTUAL"
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table

from shelf_monitor.collectors import build_collector
from shelf_monitor.collectors.cvm import (
    CvmCollector,
    discover_managers,
    load_registro_rows,
)
from shelf_monitor.config import (
    CVM_REGISTRO_ZIP_URL,
    PLATFORMS,
    Config,
)
from shelf_monitor.diff import diff_cvm, diff_shelf
from shelf_monitor.http import check_reachable
from shelf_monitor.models import (
    CvmSnapshot,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.report.dashboard import render_dashboard
from shelf_monitor.report.digest import render_digest
from shelf_monitor.storage import (
    latest_cvm_snapshot,
    latest_shelf_snapshot,
    save_cvm_snapshot,
    save_shelf_snapshot,
)

console = Console()

_STATUS_STYLE = {
    SnapshotStatus.OK: "green",
    SnapshotStatus.PARTIAL: "yellow",
    SnapshotStatus.UNAVAILABLE: "red",
}


def _status_text(status: SnapshotStatus) -> str:
    return f"[{_STATUS_STYLE[status]}]{status.value}[/]"


def _short(message: str, limit: int = 220) -> str:
    """Um coletor que tentou varias estrategias concatena os erros de todas."""
    text = " ".join(message.split())
    return text if len(text) <= limit else text[:limit] + "..."


def collect_platforms(config: Config, platform_ids: list[str]) -> list[ShelfSnapshot]:
    snapshots: list[ShelfSnapshot] = []
    for platform_id in platform_ids:
        console.print(f"[bold]Coletando prateleira:[/] {PLATFORMS[platform_id].label}")
        collector = build_collector(config, platform_id)
        snapshot = collector.collect()
        save_shelf_snapshot(config, snapshot)

        detail = f"{len(snapshot.funds)} fundos via {snapshot.strategy.value}"
        console.print(f"  {_status_text(snapshot.status)} - {detail}")
        if snapshot.error:
            console.print(f"  [dim]{_short(snapshot.error)}[/]")
        snapshots.append(snapshot)
    return snapshots


def collect_cvm(config: Config, local_zip: Path | None = None) -> CvmSnapshot:
    console.print("[bold]Coletando cadastro da CVM...[/]")
    snapshot = CvmCollector(config).collect(local_zip=local_zip)
    save_cvm_snapshot(config, snapshot)

    pre_op = sum(1 for f in snapshot.funds if f.is_pre_operational)
    console.print(
        f"  {_status_text(snapshot.status)} - {len(snapshot.funds)} fundos na "
        f"watchlist, {pre_op} em fase pre-operacional"
    )
    if snapshot.error:
        console.print(f"  [dim]{snapshot.error}[/]")
    return snapshot


def build_report(
    config: Config,
    snapshots: list[ShelfSnapshot],
    cvm_snapshot: CvmSnapshot | None,
) -> RunReport:
    report = RunReport(generated_at=datetime.now(), snapshots=snapshots)

    for snapshot in snapshots:
        previous = latest_shelf_snapshot(
            config, snapshot.platform, before=snapshot.snapshot_date
        )
        report.shelf_diffs.append(diff_shelf(snapshot, previous))

    if cvm_snapshot:
        report.cvm_snapshot = cvm_snapshot
        previous_cvm = latest_cvm_snapshot(config, before=cvm_snapshot.snapshot_date)
        report.cvm_diff = diff_cvm(cvm_snapshot, previous_cvm)

    return report


def render_outputs(config: Config, report: RunReport) -> None:
    md_path, html_path = render_digest(config, report)
    dashboard_path = render_dashboard(config, report)
    console.print()
    console.print("[bold]Relatorios gerados[/]")
    console.print(f"  digest markdown: {md_path}")
    console.print(f"  digest e-mail:   {html_path}")
    console.print(f"  dashboard:       {dashboard_path}")


def print_summary(report: RunReport) -> None:
    table = Table(title="Resumo da execucao", title_style="bold")
    table.add_column("Plataforma")
    table.add_column("Fundos", justify="right")
    table.add_column("Status")
    table.add_column("Entradas", justify="right")
    table.add_column("Saidas", justify="right")
    table.add_column("Alteracoes", justify="right")

    for snapshot in report.snapshots:
        diff = report.diff_for(snapshot.platform)
        counted = diff and not diff.is_first_run
        table.add_row(
            PLATFORMS[snapshot.platform].label
            if snapshot.platform in PLATFORMS
            else snapshot.platform,
            str(len(snapshot.funds)),
            _status_text(snapshot.status),
            str(len(diff.added)) if counted else "-",
            str(len(diff.removed)) if counted else "-",
            str(len(diff.changed)) if counted else "-",
        )

    console.print()
    console.print(table)


def git_commit(config: Config) -> None:
    """Commita apenas os artefatos versionaveis do monitor."""
    paths = [
        str(config.snapshots_dir),
        str(config.cvm_extract_dir),
        str(config.reports_dir),
    ]
    try:
        subprocess.run(["git", "add", *paths], check=True, capture_output=True)
        status = subprocess.run(
            ["git", "status", "--porcelain", *paths], check=True, capture_output=True, text=True
        )
        if not status.stdout.strip():
            console.print("[dim]Nada novo para commitar.[/]")
            return
        message = f"data: snapshot do monitor de prateleiras ({datetime.now():%Y-%m-%d})"
        subprocess.run(["git", "commit", "-m", message], check=True, capture_output=True)
        console.print(f"[green]Commit criado:[/] {message}")
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Falha no commit:[/] {exc.stderr.decode(errors='replace')[:300]}")


# --- subcomandos -------------------------------------------------------------


def cmd_run(config: Config, args: argparse.Namespace) -> None:
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)

    snapshots = collect_platforms(config, platform_ids)
    cvm_snapshot = None if args.skip_cvm else collect_cvm(config)

    report = build_report(config, snapshots, cvm_snapshot)
    print_summary(report)
    render_outputs(config, report)

    if args.git_commit:
        git_commit(config)


def cmd_collect(config: Config, args: argparse.Namespace) -> None:
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)
    snapshots = collect_platforms(config, platform_ids)
    report = build_report(config, snapshots, None)
    print_summary(report)


def cmd_cvm(config: Config, args: argparse.Namespace) -> None:
    config.ensure_dirs()
    local_zip = Path(args.local_zip) if args.local_zip else None
    snapshot = collect_cvm(config, local_zip=local_zip)

    if not snapshot.funds:
        return

    table = Table(title="Radar CVM por casa", title_style="bold")
    table.add_column("Casa")
    table.add_column("Registrados", justify="right")
    table.add_column("Pre-operacionais", justify="right")
    for house, funds in sorted(snapshot.by_house().items()):
        label = PLATFORMS[house].label if house in PLATFORMS else house
        table.add_row(
            label, str(len(funds)), str(sum(1 for f in funds if f.is_pre_operational))
        )
    console.print()
    console.print(table)


def cmd_diff(config: Config, args: argparse.Namespace) -> None:
    platform_ids = args.platforms or list(PLATFORMS)
    snapshots: list[ShelfSnapshot] = []
    for platform_id in platform_ids:
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)

    if not snapshots:
        console.print("[yellow]Nenhum snapshot encontrado. Rode `collect` antes.[/]")
        return

    cvm_snapshot = latest_cvm_snapshot(config)
    report = build_report(config, snapshots, cvm_snapshot)
    print_summary(report)


def cmd_report(config: Config, args: argparse.Namespace) -> None:
    config.ensure_dirs()
    snapshots: list[ShelfSnapshot] = []
    for platform_id in PLATFORMS:
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)

    if not snapshots:
        console.print("[yellow]Nenhum snapshot encontrado. Rode `run` antes.[/]")
        return

    report = build_report(config, snapshots, latest_cvm_snapshot(config))
    print_summary(report)
    render_outputs(config, report)


def cmd_doctor(config: Config, args: argparse.Namespace) -> None:
    """Testa o acesso a cada fonte - o gate antes da primeira execucao real."""
    sources: list[tuple[str, str]] = [
        ("CVM (cadastro de fundos)", CVM_REGISTRO_ZIP_URL),
        ("Open Finance (diretorio)", config.directory_url),
    ]
    for platform_id, platform in PLATFORMS.items():
        if platform.shelf_url:
            sources.append((f"{platform.label} (prateleira)", platform.shelf_url))

    table = Table(title="Acessibilidade das fontes", title_style="bold")
    table.add_column("Fonte")
    table.add_column("Resultado")
    table.add_column("Detalhe", overflow="fold")

    blocked = 0
    for label, url in sources:
        ok, detail = check_reachable(config, url)
        if not ok:
            blocked += 1
        table.add_row(label, "[green]ok[/]" if ok else "[red]falha[/]", detail)

    console.print(table)

    if blocked:
        console.print()
        console.print(
            f"[yellow]{blocked} de {len(sources)} fontes inacessiveis.[/] Se o detalhe "
            "indica bloqueio da network policy, libere os dominios no ambiente antes "
            "de rodar `run`."
        )

    try:
        import playwright  # noqa: F401

        console.print("[green]playwright disponivel[/] (necessario para XP e BTG)")
    except ImportError:
        console.print(
            "[yellow]playwright ausente[/] - XP e BTG so coletam via Open Finance. "
            "Instale com: pip install playwright"
        )


def cmd_watchlist_discover(config: Config, args: argparse.Namespace) -> None:
    """Lista gestores reais da CVM que casam com um termo, para refinar a watchlist."""
    config.ensure_dirs()
    local_zip = Path(args.local_zip) if args.local_zip else None
    console.print(f"[bold]Procurando gestores com[/] '{args.query}'...")

    rows = load_registro_rows(config, local_zip=local_zip)
    results = discover_managers(rows, args.query, limit=args.limit)

    if not results:
        console.print("[yellow]Nenhum gestor encontrado com esse termo.[/]")
        return

    table = Table(title=f"Gestores contendo '{args.query}'", title_style="bold")
    table.add_column("Gestor", overflow="fold")
    table.add_column("CNPJ")
    table.add_column("Raiz", style="cyan")
    table.add_column("Fundos", justify="right")
    for name, cnpj, count in results:
        table.add_row(name, cnpj or "-", (cnpj or "")[:8] or "-", str(count))
    console.print(table)
    console.print(
        "\n[dim]Adicione as raizes relevantes em WATCHLIST[casa].cnpj_roots "
        "(shelf_monitor/config.py).[/]"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Monitor de prateleiras de fundos dos concorrentes da XP"
    )
    parser.add_argument("--data-dir", help="Diretorio de dados (default: ./data)")
    subparsers = parser.add_subparsers(dest="command")

    def add_platform_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--platform",
            dest="platforms",
            action="append",
            choices=list(PLATFORMS),
            help="Plataforma a processar (repetivel; default: todas)",
        )

    run_parser = subparsers.add_parser("run", help="Coleta completa + relatorios")
    add_platform_arg(run_parser)
    run_parser.add_argument("--skip-cvm", action="store_true", help="Nao coletar a CVM")
    run_parser.add_argument(
        "--git-commit", action="store_true", help="Commitar snapshots e relatorios"
    )
    run_parser.set_defaults(func=cmd_run)

    collect_parser = subparsers.add_parser("collect", help="Coletar prateleiras")
    add_platform_arg(collect_parser)
    collect_parser.set_defaults(func=cmd_collect)

    cvm_parser = subparsers.add_parser("cvm", help="Coletar o radar CVM")
    cvm_parser.add_argument("--local-zip", help="Usar um zip local em vez de baixar")
    cvm_parser.set_defaults(func=cmd_cvm)

    diff_parser = subparsers.add_parser("diff", help="Comparar os snapshots existentes")
    add_platform_arg(diff_parser)
    diff_parser.set_defaults(func=cmd_diff)

    report_parser = subparsers.add_parser("report", help="Re-renderizar os relatorios")
    report_parser.set_defaults(func=cmd_report)

    doctor_parser = subparsers.add_parser("doctor", help="Testar acesso as fontes")
    doctor_parser.set_defaults(func=cmd_doctor)

    discover_parser = subparsers.add_parser(
        "watchlist-discover", help="Listar gestores da CVM que casam com um termo"
    )
    discover_parser.add_argument("query", help="Termo a procurar no campo Gestor")
    discover_parser.add_argument("--limit", type=int, default=30)
    discover_parser.add_argument("--local-zip", help="Usar um zip local em vez de baixar")
    discover_parser.set_defaults(func=cmd_watchlist_discover)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(1)

    config = Config(data_dir=args.data_dir)
    errors = config.validate()
    if errors:
        console.print("[red]Erros de configuracao:[/]")
        for error in errors:
            console.print(f"  - {error}")
        sys.exit(1)

    args.func(config, args)


if __name__ == "__main__":
    main()
