"""Monitor de prateleiras de fundos - CLI.

Uso:
    python -m shelf_monitor run                    # coleta tudo, diff, digest e dashboard
    python -m shelf_monitor collect --platform btg
    python -m shelf_monitor cvm                    # so o radar CVM
    python -m shelf_monitor diff
    python -m shelf_monitor report                 # re-renderiza a partir dos snapshots
    python -m shelf_monitor market                 # base geral do mercado (CVM)
    python -m shelf_monitor doctor                 # testa acesso as fontes
    python -m shelf_monitor diagnose               # sonda as fontes e grava evidencias
    python -m shelf_monitor watchlist-discover "BTG PACTUAL"
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import date, datetime
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
from shelf_monitor.diff import assess_quality, diff_cvm, diff_shelf
from shelf_monitor.enrich import enrich_snapshots
from shelf_monitor.http import check_reachable
from shelf_monitor.models import (
    CvmSnapshot,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.market.importer import run_import
from shelf_monitor.report.dashboard import render_dashboard
from shelf_monitor.report.digest import render_digest
from shelf_monitor.report.mailer import build_subject, send_digest
from shelf_monitor.report.spreadsheet import render_spreadsheet
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


# A CVM publica o cadastro todo dia util; uma base mais velha que isso nao e
# atraso da fonte, e um `market` que ninguem rodou.
_MARKET_STALE_DAYS = 45


def market_base_status(config: Config) -> tuple[str, str]:
    """Estado da base de mercado: ('ok'|'ausente'|'sem-manifesto'|'velha', detalhe).

    So leitura - serve ao `doctor`, que responde por que uma analise saiu sem
    classificacao ANBIMA antes de o operador descobrir isso no relatorio.
    """
    if not (config.market_dir / "mercado.csv").is_file():
        return "ausente", "base de mercado nao importada"

    try:
        manifest = json.loads(
            (config.market_dir / "_manifest.json").read_text(encoding="utf-8")
        )
        imported = date.fromisoformat(str(manifest.get("imported_at", "")))
    except (OSError, ValueError):
        return (
            "sem-manifesto",
            "base presente, mas _manifest.json ilegivel (idade desconhecida)",
        )

    age = (date.today() - imported).days
    if age > _MARKET_STALE_DAYS:
        return "velha", f"base importada ha {age} dias ({imported.isoformat()})"
    return "ok", f"base importada ha {age} dias"


def warn_market_missing() -> None:
    """Sem a base, a analise degrada em silencio - este e o aviso que faltava.

    Nao e so a classificacao ANBIMA que some: a particao varejo/restrito passa a
    depender so dos sinais fracos (flag da casa e token no nome), e todo numero
    principal muda de significado sem dizer nada.
    """
    console.print(
        "[yellow]Base de mercado ausente:[/] analises degradam para sinais fracos "
        "(sem classificacao ANBIMA nem gestor oficial). "
        "Rode 'python -m shelf_monitor market'."
    )


def collect_platforms(
    config: Config, platform_ids: list[str], stop_at_first_source: bool = False
) -> list[ShelfSnapshot]:
    snapshots: list[ShelfSnapshot] = []
    for platform_id in platform_ids:
        console.print(f"[bold]Coletando prateleira:[/] {PLATFORMS[platform_id].label}")
        collector = build_collector(config, platform_id, stop_at_first_source)
        snapshot = collector.collect()

        # A qualidade e avaliada contra a ultima coleta confiavel antes de
        # salvar: um snapshot truncado gravado como OK viraria a baseline do
        # dia seguinte e faria a prateleira reaparecer inteira como novidade.
        baseline = latest_shelf_snapshot(
            config, platform_id, before=snapshot.snapshot_date, trusted_only=True
        )
        assess_quality(snapshot, baseline)
        stored = save_shelf_snapshot(config, snapshot)

        sources = snapshot.source_counts or {}
        detail = f"{len(snapshot.funds)} fundos"
        if len(sources) > 1:
            # Uniao de varias fontes: mostrar o que cada uma trouxe explica por
            # que o total nao e a soma.
            parts = ", ".join(f"{name} {count}" for name, count in sources.items())
            detail += f" (uniao de {parts})"
        else:
            detail += f" via {snapshot.strategy.value}"
        console.print(f"  {_status_text(snapshot.status)} - {detail}")
        if snapshot.error:
            console.print(f"  [dim]{_short(snapshot.error)}[/]")
        if stored is None:
            console.print(
                "  [dim]snapshot do dia preservado: a coleta anterior era melhor[/]"
            )
        snapshots.append(snapshot)
    return snapshots


def collect_cvm(config: Config, local_zip: Path | None = None) -> CvmSnapshot:
    console.print("[bold]Coletando cadastro da CVM...[/]")
    snapshot = CvmCollector(config).collect(local_zip=local_zip)
    stored = save_cvm_snapshot(config, snapshot)

    pre_op = sum(1 for f in snapshot.funds if f.is_pre_operational)
    console.print(
        f"  {_status_text(snapshot.status)} - {len(snapshot.funds)} fundos na "
        f"watchlist, {pre_op} em fase pre-operacional"
    )
    if snapshot.error:
        console.print(f"  [dim]{snapshot.error}[/]")
    if stored is None:
        console.print(
            "  [dim]extrato do dia preservado: a coleta anterior era melhor[/]"
        )
    return snapshot


def build_report(
    config: Config,
    snapshots: list[ShelfSnapshot],
    cvm_snapshot: CvmSnapshot | None,
) -> RunReport:
    report = RunReport(generated_at=datetime.now(), snapshots=snapshots)

    # Normaliza contra a base oficial antes de qualquer renderer: e o que faz
    # "classe ANBIMA" significar a mesma coisa nas cinco casas.
    stats = enrich_snapshots(config, snapshots)
    if stats is not None:
        report.enrichment = stats.to_dict()
    else:
        warn_market_missing()

    for snapshot in snapshots:
        previous = latest_shelf_snapshot(
            config, snapshot.platform, before=snapshot.snapshot_date, trusted_only=True
        )
        report.shelf_diffs.append(diff_shelf(snapshot, previous))

    if cvm_snapshot:
        report.cvm_snapshot = cvm_snapshot
        previous_cvm = latest_cvm_snapshot(config, before=cvm_snapshot.snapshot_date)
        report.cvm_diff = diff_cvm(cvm_snapshot, previous_cvm)

    return report


def render_outputs(
    config: Config, report: RunReport, send_email: bool = False
) -> list[str]:
    """Renderiza as superficies e devolve as falhas das essenciais.

    Um renderer nao pode derrubar os outros nem o que vem depois dele: quando o
    dashboard quebra, o digest, o e-mail e o commit de uma coleta que levou
    dezenas de minutos ainda tem de acontecer. As falhas voltam para virar
    codigo de saida em vez de sumirem no console.
    """
    failures: list[str] = []

    md_path = html_path = dashboard_path = None
    digest_note = dashboard_note = ""
    try:
        md_path, html_path = render_digest(config, report)
    except Exception as exc:  # noqa: BLE001 - um renderer nao derruba os demais
        digest_note = f"digest: {_short(str(exc))}"
        failures.append(digest_note)
    try:
        dashboard_path = render_dashboard(config, report)
    except Exception as exc:  # noqa: BLE001
        dashboard_note = f"dashboard: {_short(str(exc))}"
        failures.append(dashboard_note)

    deck_path = None
    try:
        from shelf_monitor.report.deck import render_deck

        deck_path = render_deck(config, report)
    except ImportError:
        deck_note = "instale python-pptx para gerar o deck executivo"
    except Exception as exc:  # noqa: BLE001 - o deck nunca derruba o relatorio
        deck_note = f"falha ao gerar o deck: {_short(str(exc))}"
    console.print()
    console.print("[bold]Relatorios gerados[/]")
    if md_path:
        console.print(f"  digest markdown: {md_path}")
        console.print(f"  digest e-mail:   {html_path}")
    else:
        console.print(f"  digest:          [red]{digest_note}[/]")
    if dashboard_path:
        console.print(f"  dashboard:       {dashboard_path}")
    else:
        console.print(f"  dashboard:       [red]{dashboard_note}[/]")
    if deck_path:
        console.print(f"  deck executivo:  {deck_path}")
    else:
        console.print(f"  deck executivo:  [yellow]{deck_note}[/]")
    console.print(f"  assunto sugerido: {build_subject(report)}")

    if send_email:
        try:
            console.print(f"[bold]E-mail:[/] {send_digest(config, report)}")
        except Exception as exc:  # noqa: BLE001 - envio nao derruba a execucao
            console.print(f"[red]Falha no envio do digest:[/] {_short(str(exc))}")

    return failures


def run_outcome(
    report: RunReport, cvm_requested: bool, report_failures: list[str]
) -> tuple[str, int]:
    """Linha final da execucao e o codigo de saida que ela justifica.

    Sem isto o `run` sai 0 com as cinco casas bloqueadas, e o job aparece verde
    no cron. Uma casa PARTIAL trouxe fundos e conta como coletada; a CVM caida
    entra na linha mas nao derruba a execucao, que ainda entrega a prateleira.
    """
    total = len(report.snapshots)
    collected = sum(
        1 for s in report.snapshots if s.status != SnapshotStatus.UNAVAILABLE
    )

    if not cvm_requested:
        cvm_note = "CVM pulada"
    elif report.cvm_snapshot and report.cvm_snapshot.status != SnapshotStatus.UNAVAILABLE:
        cvm_note = "CVM ok"
    else:
        cvm_note = "CVM falhou"

    report_note = (
        "relatorios ok"
        if not report_failures
        else f"{len(report_failures)} relatorio(s) falharam"
    )

    line = f"{collected} de {total} casas coletadas, {cvm_note}, {report_note}"
    exit_code = 1 if (total and collected == 0) or report_failures else 0
    return line, exit_code


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
        subprocess.run(["git", "add", *paths], check=True, capture_output=True, text=True)
        status = subprocess.run(
            ["git", "status", "--porcelain", *paths],
            check=True,
            capture_output=True,
            text=True,
        )
        if not status.stdout.strip():
            console.print("[dim]Nada novo para commitar.[/]")
            return
        message = f"data: snapshot do monitor de prateleiras ({datetime.now():%Y-%m-%d})"
        subprocess.run(
            ["git", "commit", "-m", message], check=True, capture_output=True, text=True
        )
        console.print(f"[green]Commit criado:[/] {message}")
    except subprocess.CalledProcessError as exc:
        console.print(f"[red]Falha no commit:[/] {_short(exc.stderr or str(exc), 300)}")


# --- subcomandos -------------------------------------------------------------


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)

    snapshots = collect_platforms(config, platform_ids, args.first_source_only)
    cvm_snapshot = None if args.skip_cvm else collect_cvm(config)

    report = build_report(config, snapshots, cvm_snapshot)
    print_summary(report)
    failures = render_outputs(config, report, send_email=args.send_email)

    if args.git_commit:
        git_commit(config)

    line, exit_code = run_outcome(report, not args.skip_cvm, failures)
    console.print(f"[{'red' if exit_code else 'green'}]{line}[/]")
    return exit_code


def cmd_collect(config: Config, args: argparse.Namespace) -> None:
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)
    snapshots = collect_platforms(config, platform_ids, args.first_source_only)
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
    render_outputs(config, report, send_email=getattr(args, "send_email", False))


def cmd_export(config: Config, args: argparse.Namespace) -> None:
    """Exporta as prateleiras coletadas para uma planilha."""
    config.ensure_dirs()

    platform_ids = args.platforms or list(PLATFORMS)
    if args.collect:
        snapshots = collect_platforms(config, platform_ids, args.first_source_only)
        cvm_snapshot = None if args.skip_cvm else collect_cvm(config)
    else:
        snapshots = []
        for platform_id in platform_ids:
            snapshot = latest_shelf_snapshot(config, platform_id)
            if snapshot:
                snapshots.append(snapshot)
        cvm_snapshot = None if args.skip_cvm else latest_cvm_snapshot(config)

        if not snapshots:
            console.print(
                "[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes, ou use "
                "`export --collect` para coletar agora."
            )
            return

    enrichment = enrich_snapshots(config, snapshots)
    if enrichment is None:
        warn_market_missing()
    diffs = [
        diff_shelf(
            snapshot,
            latest_shelf_snapshot(
                config, snapshot.platform, before=snapshot.snapshot_date,
                trusted_only=True,
            ),
        )
        for snapshot in snapshots
    ]
    path = render_spreadsheet(
        config,
        snapshots,
        cvm_snapshot,
        path=Path(args.output) if args.output else None,
        diffs=diffs,
        enrichment=enrichment,
    )
    total = sum(len(s.funds) for s in snapshots)
    console.print()
    console.print(
        f"[green]Planilha gerada:[/] {path}\n"
        f"  {total} fundos de {len(snapshots)} plataforma(s)"
        + (f", {len(cvm_snapshot.funds)} registros da CVM" if cvm_snapshot else "")
    )


def cmd_market(config: Config, args: argparse.Namespace) -> None:
    """Importa a base geral do mercado a partir dos dados abertos da CVM."""
    config.ensure_dirs()
    local_dir = Path(args.local_dir) if args.local_dir else None

    console.print("[bold]Importando a base de mercado (CVM)...[/]")
    if not local_dir and not args.skip_fees:
        console.print(
            "  [dim]o extrato de informacoes tem ~100 MB; use --skip-fees para pular[/]"
        )

    database, paths = run_import(
        config, local_dir=local_dir, force=args.force, skip_fees=args.skip_fees
    )

    for note in database.notes:
        console.print(f"  [dim]{_short(note)}[/]")

    if not paths:
        console.print("[red]Nenhuma fonte respondeu - a base nao foi gerada.[/]")
        console.print("Rode `python -m shelf_monitor doctor` para ver o que esta bloqueado.")
        return

    counts = database.counts()
    table = Table(title="Base de mercado", title_style="bold")
    table.add_column("Entidade")
    table.add_column("Registros", justify="right")
    for label, key in (
        ("Fundos", "fundos"),
        ("  adaptados a RCVM 175", "fundos_adaptados"),
        ("  nao adaptados", "fundos_nao_adaptados"),
        ("Classes", "classes"),
        ("Subclasses", "subclasses"),
        ("Classes com taxa", "classes_com_taxa"),
    ):
        table.add_row(label, f"{counts[key]:,}".replace(",", "."))
    console.print()
    console.print(table)

    if database.unknown_columns:
        # Coluna nova no arquivo da CVM: a importacao seguiu, mas o dado esta
        # sendo descartado ate alguem mapea-lo.
        console.print()
        console.print("[yellow]Colunas novas na origem, ainda nao mapeadas:[/]")
        for table_name, columns in database.unknown_columns.items():
            console.print(f"  {table_name}: {', '.join(columns)}")

    console.print()
    console.print("[bold]Tabelas geradas[/]")
    for key in (
        "mercado", "hierarquia", "fundos", "classes", "subclasses", "taxas", "manifest",
    ):
        if key in paths:
            console.print(f"  {key:10} {paths[key]}")


def cmd_diagnose(config: Config, args: argparse.Namespace) -> None:
    """Sonda cada fonte de prateleira e grava a resposta bruta em disco."""
    from shelf_monitor import diagnose

    platform_ids = args.platforms or list(PLATFORMS)
    console.print(
        "[bold]Diagnosticando as fontes de prateleira...[/]\n"
        "  [dim]cada resposta e gravada em data/cache/debug/ para inspecao[/]"
    )
    if args.browser:
        console.print(
            "  [dim]modo navegador: abre cada lista num Chromium real e grava "
            "HTML renderizado, requisicoes e screenshot (leva ~1 min por site)[/]"
        )

    probes = diagnose.run(config, platform_ids, browser=args.browser)

    table = Table(title="Sondagem das fontes", title_style="bold")
    table.add_column("Fonte")
    table.add_column("OK")
    table.add_column("O que veio", overflow="fold")
    for probe in probes:
        table.add_row(
            probe.source,
            "[green]sim[/]" if probe.ok else "[red]nao[/]",
            _short(probe.detail, 110),
        )
    console.print()
    console.print(table)

    failing = [p for p in probes if not p.ok]
    console.print()
    if failing:
        console.print(
            f"[yellow]{len(failing)} de {len(probes)} sondagens sem resultado util.[/]"
        )
    console.print(
        f"Evidencias em [bold]{config.cache_dir / 'debug'}[/] - "
        "o relatorio consolidado esta em diagnostico.json."
    )
    console.print(
        "[dim]Uma pagina que responde 200 mas nao traz CNPJ nem tabela e casca de "
        "SPA: os dados chegam por JavaScript e so o Playwright os alcanca.[/]"
    )


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

    state, detail = market_base_status(config)
    if state == "ok":
        console.print(f"[green]base de mercado disponivel[/] ({detail})")
    else:
        console.print(
            f"[yellow]base de mercado {state}[/] - {detail}. Sem ela a comparacao "
            "perde a classificacao ANBIMA oficial e a particao varejo/restrito "
            "cai nos sinais fracos. Rode: python -m shelf_monitor market"
        )

    try:
        import playwright  # noqa: F401

        console.print(
            "[green]playwright disponivel[/] (necessario para XP, BTG e Itau - "
            "as tres listas so existem depois do JavaScript)"
        )
    except ImportError:
        console.print(
            "[yellow]playwright ausente[/] - XP, BTG e Itau so coletam via Open "
            "Finance. Instale com: pip install playwright"
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
    # --data-dir vale antes ou depois do subcomando: exigir uma das duas ordens
    # e uma pegadinha desnecessaria na linha de comando. O SUPPRESS e o que
    # impede o subparser de sobrescrever com None o valor dado antes dele.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--data-dir",
        default=argparse.SUPPRESS,
        help="Diretorio de dados (default: ./data)",
    )

    parser = argparse.ArgumentParser(
        description="Monitor de prateleiras de fundos dos concorrentes da XP",
        parents=[common],
    )
    subparsers = parser.add_subparsers(dest="command")

    def add_collect_args(sub: argparse.ArgumentParser) -> None:
        """Opcoes de quem coleta: por padrao consulta todas as fontes da casa."""
        sub.add_argument(
            "--first-source-only",
            action="store_true",
            help=(
                "Parar na primeira fonte util de cada casa, em vez de unir todas "
                "(mais rapido, cobertura menor)"
            ),
        )

    def add_platform_arg(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--platform",
            dest="platforms",
            action="append",
            choices=list(PLATFORMS),
            help="Plataforma a processar (repetivel; default: todas)",
        )

    run_parser = subparsers.add_parser("run", help="Coleta completa + relatorios", parents=[common])
    add_platform_arg(run_parser)
    add_collect_args(run_parser)
    run_parser.add_argument("--skip-cvm", action="store_true", help="Nao coletar a CVM")
    run_parser.add_argument(
        "--git-commit", action="store_true", help="Commitar snapshots e relatorios"
    )
    run_parser.add_argument(
        "--send-email",
        action="store_true",
        help="Enviar o digest por SMTP (requer SMTP_HOST, SMTP_FROM e SMTP_TO)",
    )
    run_parser.set_defaults(func=cmd_run)

    collect_parser = subparsers.add_parser("collect", help="Coletar prateleiras", parents=[common])
    add_platform_arg(collect_parser)
    add_collect_args(collect_parser)
    collect_parser.set_defaults(func=cmd_collect)

    cvm_parser = subparsers.add_parser("cvm", help="Coletar o radar CVM", parents=[common])
    cvm_parser.add_argument("--local-zip", help="Usar um zip local em vez de baixar")
    cvm_parser.set_defaults(func=cmd_cvm)

    diff_parser = subparsers.add_parser("diff", help="Comparar os snapshots existentes", parents=[common])
    add_platform_arg(diff_parser)
    diff_parser.set_defaults(func=cmd_diff)

    report_parser = subparsers.add_parser("report", help="Re-renderizar os relatorios", parents=[common])
    report_parser.add_argument(
        "--send-email", action="store_true", help="Enviar o digest por SMTP"
    )
    report_parser.set_defaults(func=cmd_report)

    export_parser = subparsers.add_parser(
        "export", help="Exportar as prateleiras para planilha (.xlsx)", parents=[common]
    )
    add_platform_arg(export_parser)
    add_collect_args(export_parser)
    export_parser.add_argument(
        "--collect",
        action="store_true",
        help="Coletar agora em vez de usar os ultimos snapshots salvos",
    )
    export_parser.add_argument("--skip-cvm", action="store_true", help="Nao incluir a CVM")
    export_parser.add_argument("-o", "--output", help="Caminho do arquivo .xlsx")
    export_parser.set_defaults(func=cmd_export)

    market_parser = subparsers.add_parser(
        "market",
        help="Importar a base geral do mercado (CVM) para tabelas CSV",
        parents=[common],
    )
    market_parser.add_argument(
        "--skip-fees",
        action="store_true",
        help="Nao baixar o extrato de informacoes (~100 MB) - base sem taxas",
    )
    market_parser.add_argument(
        "--force", action="store_true", help="Rebaixar mesmo sem mudanca na origem"
    )
    market_parser.add_argument(
        "--local-dir", help="Usar arquivos ja baixados neste diretorio"
    )
    market_parser.set_defaults(func=cmd_market)

    diagnose_parser = subparsers.add_parser(
        "diagnose",
        help="Sondar as fontes de prateleira e gravar as respostas para analise",
        parents=[common],
    )
    add_platform_arg(diagnose_parser)
    diagnose_parser.add_argument(
        "--browser",
        action="store_true",
        help=(
            "Abrir cada lista num Chromium real e gravar HTML renderizado, "
            "requisicoes XHR e screenshot (lento, mas e o que ve a lista de fato)"
        ),
    )
    diagnose_parser.set_defaults(func=cmd_diagnose)

    doctor_parser = subparsers.add_parser("doctor", help="Testar acesso as fontes", parents=[common])
    doctor_parser.set_defaults(func=cmd_doctor)

    discover_parser = subparsers.add_parser("watchlist-discover", help="Listar gestores da CVM que casam com um termo", parents=[common])
    discover_parser.add_argument("query", help="Termo a procurar no campo Gestor")
    discover_parser.add_argument("--limit", type=int, default=30)
    discover_parser.add_argument("--local-zip", help="Usar um zip local em vez de baixar")
    discover_parser.set_defaults(func=cmd_watchlist_discover)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        sys.exit(1)

    config = Config(data_dir=getattr(args, "data_dir", None))
    errors = config.validate()
    if errors:
        console.print("[red]Erros de configuracao:[/]")
        for error in errors:
            console.print(f"  - {error}")
        sys.exit(1)

    # So o `run` devolve codigo; os demais subcomandos seguem saindo 0.
    exit_code = args.func(config, args)
    if exit_code:
        sys.exit(exit_code)


if __name__ == "__main__":
    main()
