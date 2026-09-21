"""Monitor de prateleiras de fundos - CLI.

Uso:
    python -m shelf_monitor run                    # coleta tudo, diff, digest e dashboard
    python -m shelf_monitor collect --platform btg
    python -m shelf_monitor cvm                    # so o radar CVM
    python -m shelf_monitor diff
    python -m shelf_monitor report                 # re-renderiza a partir dos snapshots
    python -m shelf_monitor insights               # leitura competitiva das vitrines
    python -m shelf_monitor captacao               # captacao de varejo e oportunidades de grade
    python -m shelf_monitor performance            # rentabilidade ponderada por PL e cotistas
    python -m shelf_monitor rentabilidade          # retorno por fundo em 1m, 3m, YTD, 12m e 24m
    python -m shelf_monitor custo                  # taxa de cada fundo contra a categoria dele
    python -m shelf_monitor mandato                # o que o regulamento de cada classe autoriza
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
    CollectorStrategy,
    CvmSnapshot,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.market import etf_sources, fundosnet
from shelf_monitor.market.etf import build_etf_catalog
from shelf_monitor.market.etf_flow import (
    aggregate_category_weekly_flows,
    aggregate_etf_weekly_flows,
    flow_coverage,
    get_recent_months,
    run_etf_flow_pipeline,
)
from shelf_monitor.market.fie_balancete import run_etf_pl_pipeline
from shelf_monitor.market.importer import run_import
from shelf_monitor.report.competitive import (
    build_competitive_view,
    print_competitive_view,
)
from shelf_monitor.report.retail_flow import (
    build_grade_opportunities,
    build_retail_flow,
    print_retail_flow,
)
from shelf_monitor.report.dashboard import render_dashboard
from shelf_monitor.report.digest import render_digest
from shelf_monitor.report.etf_report import (
    export_etf_csv,
    export_etf_excel,
    print_etf_pl,
    print_etf_summary,
)
from shelf_monitor.report.mailer import build_subject, send_digest
from shelf_monitor.report.custo import build_custo, print_custo
from shelf_monitor.report.mandato import build_mandato, print_mandato
from shelf_monitor.report.performance import build_performance, print_performance
from shelf_monitor.report.rentabilidade import (
    build_rentabilidade,
    print_rentabilidade,
)
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

    Nao e so a classificacao ANBIMA que some: a gestora deixa de vir do
    registro (fica so o que a casa publicou, quando publicou), a particao
    varejo/restrito passa a depender so dos sinais fracos (flag da casa e token
    no nome), e todo numero principal muda de significado sem dizer nada.
    """
    console.print(
        "[yellow]Base de mercado ausente:[/] analises degradam para sinais fracos "
        "(sem classificacao ANBIMA nem gestora oficial). "
        "Rode 'python -m shelf_monitor market' (ou 'market --skip-fees', mais leve)."
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


def ensure_market_base(config: Config) -> None:
    """Importa a base de mercado quando ela ainda nao existe em disco.

    O download completo so acontece na primeira vez: depois o cache
    condicional do modulo market resolve com um 304. Uma falha aqui nunca
    derruba a coleta - o relatorio sai sem enriquecimento, com o aviso.

    Base velha nao e reimportada aqui: quem denuncia a idade e o `doctor`, e
    decidir por conta propria baixar 126 MB no meio de uma coleta seria uma
    surpresa cara.
    """
    state, _ = market_base_status(config)
    if state != "ausente":
        return

    console.print("[bold]Base de mercado ausente - importando da CVM...[/]")
    try:
        database, paths = run_import(config)
    except Exception as exc:  # noqa: BLE001 - a coleta do dia vale mais
        console.print(f"[yellow]Falha ao importar a base: {_short(str(exc))}[/]")
        return

    for note in database.notes:
        console.print(f"  [dim]{_short(note)}[/]")
    if not paths:
        console.print("[yellow]Nenhuma fonte da CVM respondeu.[/]")


def build_report(
    config: Config,
    snapshots: list[ShelfSnapshot],
    cvm_snapshot: CvmSnapshot | None,
    *,
    with_rentabilidade: bool = False,
) -> RunReport:
    report = RunReport(generated_at=datetime.now(), snapshots=snapshots)

    # Normaliza contra a base oficial antes de qualquer renderer: e o que faz
    # "classe ANBIMA" significar a mesma coisa nas cinco casas.
    stats = enrich_snapshots(config, snapshots)
    if stats is None:
        warn_market_missing()
    else:
        report.enrichment = stats.to_dict()
        report.enrichment_stats = stats

    for snapshot in snapshots:
        previous = latest_shelf_snapshot(
            config, snapshot.platform, before=snapshot.snapshot_date, trusted_only=True
        )
        report.shelf_diffs.append(diff_shelf(snapshot, previous))

    if cvm_snapshot:
        report.cvm_snapshot = cvm_snapshot
        previous_cvm = latest_cvm_snapshot(config, before=cvm_snapshot.snapshot_date)
        report.cvm_diff = diff_cvm(cvm_snapshot, previous_cvm)

    attach_retail_flow(config, report)
    attach_performance(config, report)
    attach_custo_e_mandato(config, report)
    # Opt-in, como `--with-etf` e pela mesma razao: a primeira montagem le ate
    # 24 competencias do informe (~2,5 min e ~260 MB). Rodando sempre, todo
    # `run`, `collect`, `diff`, `report` e `export` pagava esse custo - contra
    # o que o proprio contrato em `models.RunReport.rentabilidade` promete
    # ("o `run` so a calcula quando pedida").
    if with_rentabilidade:
        attach_rentabilidade(config, report)

    return report


def attach_retail_flow(config: Config, report: RunReport) -> None:
    """Anexa a captacao dos fundos de vitrine ao relatorio.

    Nunca derruba a execucao: o informe diario e uma fonte externa, e uma coleta
    de prateleira que levou dezenas de minutos nao pode morrer porque a CVM
    estava fora do ar. Sem ele, as superficies simplesmente omitem o bloco.
    """
    try:
        flow = build_retail_flow(config, report.snapshots)
    except Exception as exc:  # noqa: BLE001 - fonte externa, bloco opcional
        console.print(
            f"[yellow]Captacao nao calculada:[/] {_short(str(exc))} "
            "(os demais blocos seguem normalmente)"
        )
        return

    if not flow.get("available"):
        # Anexa mesmo assim: os renderers ja checam `available`, e o que se
        # perdia era o MOTIVO. Sem isto o campo ficava None, `aggregate._bloco`
        # recebia None e o dashboard caia num "sem base de mercado" generico -
        # mesmo quando a causa real era outra (categoria sem amostra, rede
        # fora). O motivo existia, era impresso aqui, e morria antes da
        # superficie que um comite le.
        console.print(f"[yellow]Captacao nao calculada:[/] {flow.get('reason')}")
        report.retail_flow = flow
        return

    report.retail_flow = flow
    try:
        report.grade_opportunities = build_grade_opportunities(
            report.snapshots, flow["_fluxos"]
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]Oportunidades de grade nao calculadas:[/] {_short(str(exc))}")


def attach_performance(config: Config, report: RunReport) -> None:
    """Anexa a rentabilidade ponderada ao relatorio.

    Mesma politica do fluxo: nunca derruba a execucao, e quando a fonte falta a
    superficie omite o bloco. Custa mais que a captacao porque le o informe do
    universo inteiro - a mediana de referencia por categoria precisa do mercado,
    nao so da vitrine.
    """
    try:
        performance = build_performance(config, report.snapshots)
    except Exception as exc:  # noqa: BLE001 - fonte externa, bloco opcional
        console.print(
            f"[yellow]Rentabilidade nao calculada:[/] {_short(str(exc))} "
            "(os demais blocos seguem normalmente)"
        )
        return

    if not performance.get("available"):
        console.print(
            f"[yellow]Rentabilidade nao calculada:[/] {performance.get('reason')}"
        )
    report.performance = performance


def attach_rentabilidade(config: Config, report: RunReport) -> None:
    """Anexa a rentabilidade por janela ao relatorio.

    Custa a leitura de ate 24 competencias do informe na primeira vez; depois o
    resumo mensal vem do cache e so a competencia corrente e refeita. Como todo
    bloco opcional, uma falha aqui nao derruba a execucao.
    """
    serie_dir = config.cache_dir / "serie"
    if not serie_dir.exists():
        console.print(
            "[bold]Montando a serie mensal de 24 competencias[/] pela primeira vez "
            "(~2,5 min e ~260 MB). Nas proximas execucoes so a competencia "
            f"corrente e refeita - o resto vem de {serie_dir}."
        )
    try:
        bloco = build_rentabilidade(config, report.snapshots)
    except Exception as exc:  # noqa: BLE001 - bloco opcional
        console.print(f"[yellow]Rentabilidade por fundo nao calculada:[/] {_short(str(exc))}")
        return
    if not bloco.get("available"):
        console.print(
            f"[yellow]Rentabilidade por fundo nao calculada:[/] {bloco.get('reason')}"
        )
    report.rentabilidade = bloco


def attach_custo_e_mandato(config: Config, report: RunReport) -> None:
    """Anexa custo relativo e mandato declarado ao relatorio.

    Os dois saem do extrato da CVM ja em disco, entao nao dependem de rede - mas
    dependem da base importada. Sem ela o bloco e omitido com motivo, como todos
    os outros.
    """
    for nome, construtor, destino in (
        ("Custo", build_custo, "custo"),
        ("Mandato", build_mandato, "mandato"),
    ):
        try:
            bloco = construtor(config, report.snapshots)
        except Exception as exc:  # noqa: BLE001 - bloco opcional
            console.print(f"[yellow]{nome} nao calculado:[/] {_short(str(exc))}")
            continue
        if not bloco.get("available"):
            console.print(f"[yellow]{nome} nao calculado:[/] {bloco.get('reason')}")
        setattr(report, destino, bloco)


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

    # A planilha sai aqui junto das demais superficies. Antes ela existia so no
    # subcomando `export`, e o `run` terminava anunciando "Relatorios gerados"
    # sem ela - quem esperava a planilha do dia concluia que a geracao falhara.
    # Mesma entrada do `export`: os snapshots, os diffs e o enriquecimento que o
    # report ja carrega.
    spreadsheet_path = None
    spreadsheet_note = ""
    try:
        spreadsheet_path = render_spreadsheet(
            config,
            report.snapshots,
            report.cvm_snapshot,
            generated_at=report.generated_at,
            diffs=report.shelf_diffs,
            enrichment=report.enrichment_stats,
            retail_flow=report.retail_flow,
            grade=report.grade_opportunities,
            performance=report.performance,
            rentabilidade=report.rentabilidade,
            custo=report.custo,
            mandato=report.mandato,
        )
    except ImportError:
        spreadsheet_note = "instale openpyxl para gerar a planilha"
    except Exception as exc:  # noqa: BLE001 - a planilha nao derruba o relatorio
        spreadsheet_note = f"falha ao gerar a planilha: {_short(str(exc))}"

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
    if spreadsheet_path:
        console.print(f"  planilha:        {spreadsheet_path}")
    else:
        console.print(f"  planilha:        [yellow]{spreadsheet_note}[/]")
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


def run_etf_monitor(config: Config) -> None:
    """Catálogo + fluxo de ETF, chamado por `run --with-etf` e por `etf`.

    Opt-in no `run` (não roda por padrão): baixa até 3 meses de INF_DIARIO -
    tráfego extra que a coleta diária de prateleiras não pedia antes. Uma
    falha aqui não derruba a coleta - mesmo contrato de `render_outputs`.
    """
    try:
        console.print()
        console.print("[bold]Catalogando ETFs na CVM e analisando fluxo...[/]")
        catalog, weekly_flows, category_summaries = run_etf_flow_pipeline(config)
        coverage = flow_coverage(catalog, weekly_flows)
        print_etf_summary(weekly_flows, category_summaries, catalog, coverage=coverage)
        pl_series, pl_cov, pl_missing = run_etf_pl_pipeline(
            config, catalog, get_recent_months(4)
        )
        print_etf_pl(pl_series, pl_cov, pl_missing)
        export_etf_excel(
            weekly_flows, category_summaries, catalog,
            config.reports_dir / "etf_captacao_semanal.xlsx",
            pl_series=pl_series,
        )
        export_etf_csv(weekly_flows, config.reports_dir / "etf_weekly_flow.csv")
    except Exception as exc:  # noqa: BLE001 - ETF e opcional, nao pode derrubar o run
        console.print(f"[yellow]Falha no monitoramento de ETF:[/] {_short(str(exc))}")


def cmd_run(config: Config, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)

    snapshots = collect_platforms(config, platform_ids, args.first_source_only)
    cvm_snapshot = None if args.skip_cvm else collect_cvm(config)

    if not args.no_market:
        ensure_market_base(config)

    report = build_report(
        config, snapshots, cvm_snapshot, with_rentabilidade=getattr(args, "with_rentabilidade", False)
    )
    print_summary(report)
    failures = render_outputs(config, report, send_email=args.send_email)

    if args.with_etf:
        run_etf_monitor(config)

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
    # `report` era o unico comando que lia snapshot e nao aceitava --platform:
    # ignorava o filtro em silencio e re-renderizava as cinco casas.
    snapshots: list[ShelfSnapshot] = []
    for platform_id in args.platforms or list(PLATFORMS):
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)

    if not snapshots:
        console.print("[yellow]Nenhum snapshot encontrado. Rode `run` antes.[/]")
        return

    report = build_report(
        config, snapshots, latest_cvm_snapshot(config),
        with_rentabilidade=getattr(args, "with_rentabilidade", False),
    )
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
    # A planilha avulsa recebe os mesmos blocos da do `run`: um `export` que
    # sai sem captacao seria uma segunda versao do mesmo arquivo.
    avulso = RunReport(generated_at=datetime.now(), snapshots=snapshots)
    attach_retail_flow(config, avulso)
    attach_performance(config, avulso)
    attach_custo_e_mandato(config, avulso)
    if getattr(args, "with_rentabilidade", False):
        attach_rentabilidade(config, avulso)

    path = render_spreadsheet(
        config,
        snapshots,
        cvm_snapshot,
        path=Path(args.output) if args.output else None,
        diffs=diffs,
        enrichment=enrichment,
        retail_flow=avulso.retail_flow,
        grade=avulso.grade_opportunities,
        performance=avulso.performance,
        rentabilidade=avulso.rentabilidade,
        custo=avulso.custo,
        mandato=avulso.mandato,
    )
    total = sum(len(s.funds) for s in snapshots)
    console.print()
    console.print(
        f"[green]Planilha gerada:[/] {path}\n"
        f"  {total} fundos de {len(snapshots)} plataforma(s)"
        + (f", {len(cvm_snapshot.funds)} registros da CVM" if cvm_snapshot else "")
    )


def cmd_insights(config: Config, args: argparse.Namespace) -> None:
    """Leitura competitiva das vitrines a partir dos snapshots gravados.

    Nao coleta: le o ultimo snapshot de cada casa. Assim a analise pode ser
    reexecutada e discutida quantas vezes for preciso sem bater nos sites - e o
    que separa "rodar o monitor" de "olhar o resultado".
    """
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)

    snapshots: list[ShelfSnapshot] = []
    for platform_id in platform_ids:
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)

    if not snapshots:
        console.print(
            "[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes - a leitura "
            "competitiva e feita sobre o que ja foi coletado."
        )
        return

    if enrich_snapshots(config, snapshots) is None:
        warn_market_missing()

    datas = sorted({s.snapshot_date for s in snapshots})
    console.print(
        f"[bold]Leitura competitiva de {len(snapshots)} casa(s)[/] - snapshots de "
        + ", ".join(d.isoformat() for d in datas)
    )

    view = build_competitive_view(config, snapshots)
    print_competitive_view(view)

    if args.json:
        destino = Path(args.json)
        # `_positions`, `_reaches` e `_decisoes` carregam os objetos para quem
        # chama em Python; no JSON sairiam como lixo nao serializavel.
        limpo = {
            bloco: {k: v for k, v in dados.items() if not k.startswith("_")}
            if isinstance(dados, dict) else dados
            for bloco, dados in view.items()
        }
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(limpo, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        console.print()
        console.print(f"[green]Visao em JSON:[/] {destino}")


def cmd_captacao(config: Config, args: argparse.Namespace) -> None:
    """Captacao de varejo dos fundos de vitrine, e o que isso abre de grade.

    Usa os snapshots ja gravados mais o informe diario da CVM: o fluxo vem do
    INF_DIARIO, a vitrine vem da ultima coleta de cada casa.
    """
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)

    snapshots: list[ShelfSnapshot] = []
    for platform_id in platform_ids:
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)

    if not snapshots:
        console.print(
            "[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes - a analise de "
            "captacao parte da vitrine ja coletada."
        )
        return

    if enrich_snapshots(config, snapshots) is None:
        warn_market_missing()

    console.print(
        f"[bold]Lendo o informe diario da CVM[/] para os fundos de vitrine "
        f"({args.months} meses)..."
    )
    resultado = build_retail_flow(
        config, snapshots, meses=args.months, force_download=args.force
    )
    oportunidades = (
        build_grade_opportunities(snapshots, resultado["_fluxos"])
        if resultado["available"] else {"available": False, "reason": resultado["reason"]}
    )
    print_retail_flow(resultado, oportunidades)

    if args.json:
        destino = Path(args.json)
        limpo = {
            "captacao": {k: v for k, v in resultado.items() if not k.startswith("_")},
            "oportunidades": {
                k: v for k, v in oportunidades.items() if not k.startswith("_")
            },
        }
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(limpo, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        console.print()
        console.print(f"[green]Analise em JSON:[/] {destino}")


def cmd_performance(config: Config, args: argparse.Namespace) -> None:
    """Rentabilidade da vitrine ponderada por PL e por numero de cotistas.

    Responde se os fundos onde estao o dinheiro e as pessoas rendem de forma
    adequada - "adequada" medido contra a mediana da mesma categoria no
    universo oficial, nao contra a propria prateleira.
    """
    config.ensure_dirs()
    platform_ids = args.platforms or list(PLATFORMS)

    snapshots: list[ShelfSnapshot] = []
    for platform_id in platform_ids:
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)

    if not snapshots:
        console.print(
            "[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes - a leitura "
            "de rentabilidade parte da vitrine ja coletada."
        )
        return

    if enrich_snapshots(config, snapshots) is None:
        warn_market_missing()

    console.print(
        f"[bold]Lendo o informe diario da CVM[/] para o universo inteiro "
        f"({args.months} meses) - a mediana de referencia precisa do mercado, "
        "nao so da vitrine..."
    )
    resultado = build_performance(
        config, snapshots, meses=args.months, force_download=args.force
    )
    print_performance(resultado)

    if args.json:
        destino = Path(args.json)
        limpo = {k: v for k, v in resultado.items() if not k.startswith("_")}
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(
            json.dumps(limpo, indent=2, ensure_ascii=False, default=str) + "\n",
            encoding="utf-8",
        )
        console.print()
        console.print(f"[green]Analise em JSON:[/] {destino}")


def cmd_rentabilidade(config: Config, args: argparse.Namespace) -> None:
    """Rentabilidade de cada fundo de vitrine em 1m, 3m, YTD, 12m e 24m."""
    snapshots = _snapshots_enriquecidos(config, args)
    if not snapshots:
        console.print("[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes.")
        return

    console.print(
        f"[bold]Montando a serie mensal[/] de ate {args.months} competencias. "
        "Competencia ja resumida vem do cache em data/cache/serie/; a corrente e "
        "sempre refeita."
    )

    def progresso(yyyymm: str, quantos: int | None) -> None:
        if quantos is None:
            console.print(f"  [yellow]{yyyymm}: indisponivel[/]")

    resultado = build_rentabilidade(
        config, snapshots, meses=args.months,
        force_download=args.force, progresso=progresso,
    )
    print_rentabilidade(resultado)
    _talvez_json(args, resultado)


def _snapshots_enriquecidos(
    config: Config, args: argparse.Namespace
) -> list[ShelfSnapshot]:
    """Vitrines ja coletadas, enriquecidas. Vazio quando nao ha snapshot."""
    config.ensure_dirs()
    snapshots: list[ShelfSnapshot] = []
    for platform_id in args.platforms or list(PLATFORMS):
        snapshot = latest_shelf_snapshot(config, platform_id)
        if snapshot:
            snapshots.append(snapshot)
    if not snapshots:
        return []
    if enrich_snapshots(config, snapshots) is None:
        warn_market_missing()
    return snapshots


def cmd_custo(config: Config, args: argparse.Namespace) -> None:
    """Custo da vitrine relativo a mediana da categoria de cada fundo."""
    snapshots = _snapshots_enriquecidos(config, args)
    if not snapshots:
        console.print("[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes.")
        return
    resultado = build_custo(config, snapshots)
    print_custo(resultado)
    _talvez_json(args, resultado)


def cmd_mandato(config: Config, args: argparse.Namespace) -> None:
    """Mandato declarado das vitrines - o que o regulamento autoriza."""
    snapshots = _snapshots_enriquecidos(config, args)
    if not snapshots:
        console.print("[yellow]Nenhum snapshot encontrado.[/] Rode `run` antes.")
        return
    resultado = build_mandato(config, snapshots)
    print_mandato(resultado)
    _talvez_json(args, resultado)


def _talvez_json(args: argparse.Namespace, resultado: dict) -> None:
    if not getattr(args, "json", None):
        return
    destino = Path(args.json)
    limpo = {k: v for k, v in resultado.items() if not k.startswith("_")}
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(limpo, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    console.print()
    console.print(f"[green]Analise em JSON:[/] {destino}")


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
    # (label, url, o navegador cobre esta fonte?). A terceira coluna existe porque
    # a prateleira de uma casa com CDN anti-bot responde 403 a este teste - que e
    # HTTP simples - e coleta normalmente pelo Playwright. Contar esse 403 como
    # fonte inacessivel mandava procurar bloqueio de rede onde nao havia.
    sources: list[tuple[str, str, bool]] = [
        ("CVM (cadastro de fundos)", CVM_REGISTRO_ZIP_URL, False),
        ("Open Finance (diretorio)", config.directory_url, False),
    ]
    for platform_id, platform in PLATFORMS.items():
        if platform.shelf_url:
            sources.append((
                f"{platform.label} (prateleira)",
                platform.shelf_url,
                CollectorStrategy.PLAYWRIGHT in platform.strategies,
            ))

    table = Table(title="Acessibilidade das fontes", title_style="bold")
    table.add_column("Fonte")
    table.add_column("Resultado")
    table.add_column("Detalhe", overflow="fold")

    blocked = 0
    via_browser = 0
    for label, url, browser_covers in sources:
        ok, detail = check_reachable(config, url)
        if ok:
            result = "[green]ok[/]"
        elif browser_covers:
            via_browser += 1
            result = "[cyan]so navegador[/]"
            detail = f"{detail} - esperado; a coleta usa Playwright"
        else:
            blocked += 1
            result = "[red]falha[/]"
        table.add_row(label, result, detail)

    console.print(table)

    if via_browser:
        console.print()
        console.print(
            f"[cyan]{via_browser} prateleira(s) so abrem em navegador.[/] Nao e "
            "bloqueio: a CDN da casa barra cliente HTTP, e o Playwright coleta. "
            "Exige o Chromium completo - o headless shell tambem leva 403."
        )

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


def print_probes(probes: list) -> None:
    """Tabela de sondagens, no mesmo formato do `diagnose` das prateleiras."""
    table = Table(title="Sondagem das fontes de ETF", title_style="bold")
    table.add_column("Fonte")
    table.add_column("OK")
    table.add_column("O que veio", overflow="fold")
    for probe in probes:
        table.add_row(
            probe.source,
            "[green]sim[/]" if probe.ok else "[red]nao[/]",
            _short(probe.detail, 160),
        )
    console.print()
    console.print(table)
    failing = [p for p in probes if not p.ok]
    if failing:
        console.print(f"[yellow]{len(failing)} de {len(probes)} sondagens sem resultado útil.[/]")
    console.print()


def cmd_etf(config: Config, args: argparse.Namespace) -> None:
    """Monitora os ETFs registrados na CVM: catálogo completo (todas as
    categorias, incluindo Renda Fixa) + a captação semanal que a fonte
    disponível de fato cobre.
    """
    config.ensure_dirs()

    if args.probe_sources:
        console.print("[bold]Sondando as fontes que cobrem ETF (Fundos.NET, B3, COTAHIST)...[/]")
        console.print(f"  [dim]evidência bruta em {config.etf_sources_debug_dir}[/]")
        probes = etf_sources.run_probes(config, build_etf_catalog(config))
        print_probes(probes)
        console.print(
            "[dim]Próximo passo: com as sondagens OK, `etf --collect-sources b3,fnet,cotahist` "
            "grava a foto do dia e `etf --flow-source fnet` agrega o fluxo a partir dela.[/]"
        )
        return

    if args.collect_sources:
        sources = [s.strip() for s in args.collect_sources.split(",") if s.strip()]
        console.print(f"[bold]Coletando fontes de ETF: {', '.join(sources)}...[/]")
        summary = etf_sources.collect_sources(config, build_etf_catalog(config), sources)
        for name, info in summary.items():
            style = "red" if "erro" in info else "green"
            console.print(f"  [{style}]{name}[/]: {info}")
        console.print()

    if args.flow_source == "fnet":
        console.print("[bold]Agregando o fluxo a partir dos informes diários do Fundos.NET...[/]")
        catalog = build_etf_catalog(config)
        records = fundosnet.records_from_informes(fundosnet.read_fnet_csvs(config))
        if not records:
            console.print(
                "[yellow]Nenhum informe coletado em data/etf/fnet/. Rode "
                "`etf --collect-sources fnet` (depois de `etf --probe-sources`).[/]"
            )
        weekly_flows = aggregate_etf_weekly_flows(catalog, records)
        category_summaries = aggregate_category_weekly_flows(weekly_flows)
    else:
        console.print(f"[bold]Catalogando ETFs na CVM e analisando fluxo (últimos {args.months} meses)...[/]")
        catalog, weekly_flows, category_summaries = run_etf_flow_pipeline(
            config, months_count=args.months, force_download=args.force
        )
    coverage = flow_coverage(catalog, weekly_flows)

    # O catálogo sai sempre - é a resposta a "o monitor enxerga todos os
    # ETFs?" - mesmo quando a captação semanal (fonte à parte, com cobertura
    # própria) não tem nada a mostrar.
    print_etf_summary(
        weekly_flows, category_summaries, catalog, coverage=coverage,
        source_label="Fundos.NET" if args.flow_source == "fnet" else "INF_DIARIO",
    )

    # PL mensal do balancete de FIE: a única fonte medida que alcança o ETF em
    # si, e não só os feeders. Roda sempre - são ~0,1 MB por competência, ordens
    # de grandeza abaixo do INF_DIARIO, então não há o que tornar opt-in.
    pl_series, pl_cov, pl_missing = run_etf_pl_pipeline(
        config, catalog, get_recent_months(args.months + 1), force_download=args.force
    )
    print_etf_pl(pl_series, pl_cov, pl_missing)

    if args.export or args.output:
        xlsx_path = Path(args.output) if args.output else (config.reports_dir / "etf_captacao_semanal.xlsx")
        csv_path = config.reports_dir / "etf_weekly_flow.csv"

        saved_xlsx = export_etf_excel(
            weekly_flows, category_summaries, catalog, xlsx_path, pl_series=pl_series
        )
        saved_csv = export_etf_csv(weekly_flows, csv_path)

        console.print()
        console.print("[green]Arquivos exportados com sucesso:[/]")
        console.print(f"  Planilha Excel (catálogo completo + fluxo): {saved_xlsx}")
        console.print(f"  Arquivo CSV (fluxo semanal):                {saved_csv}")


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
        "--no-market",
        action="store_true",
        help="Nao importar a base de mercado quando ela faltar (sai sem gestora oficial)",
    )
    run_parser.add_argument(
        "--git-commit", action="store_true", help="Commitar snapshots e relatorios"
    )
    run_parser.add_argument(
        "--send-email",
        action="store_true",
        help="Enviar o digest por SMTP (requer SMTP_HOST, SMTP_FROM e SMTP_TO)",
    )
    run_parser.add_argument(
        "--with-etf",
        action="store_true",
        help=(
            "Tambem catalogar ETFs (todas as categorias, incl. Renda Fixa) e "
            "analisar fluxo semanal - opt-in: baixa ate 3 meses de INF_DIARIO "
            "alem da coleta normal"
        ),
    )
    run_parser.add_argument(
        "--with-rentabilidade",
        action="store_true",
        help=(
            "Tambem calcular o retorno de cada fundo em 1m/3m/YTD/12m/24m - "
            "opt-in: a primeira montagem le ate 24 competencias do INF_DIARIO "
            "(~2,5 min e ~260 MB); depois vem do cache em data/cache/serie"
        ),
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
    add_platform_arg(report_parser)
    report_parser.add_argument(
        "--send-email", action="store_true", help="Enviar o digest por SMTP"
    )
    report_parser.add_argument(
        "--with-rentabilidade",
        action="store_true",
        help="Tambem calcular o retorno por fundo em janelas (ver `run --help`)",
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
    export_parser.add_argument(
        "--with-rentabilidade",
        action="store_true",
        help="Tambem incluir a aba de retorno por fundo (ver `run --help`)",
    )
    export_parser.set_defaults(func=cmd_export)

    insights_parser = subparsers.add_parser(
        "insights",
        help="Leitura competitiva das vitrines: posicionamento, gestoras, momentum e decisoes",
        parents=[common],
    )
    add_platform_arg(insights_parser)
    insights_parser.add_argument(
        "--json", help="Tambem gravar a visao completa neste caminho (.json)"
    )
    insights_parser.set_defaults(func=cmd_insights)

    captacao_parser = subparsers.add_parser(
        "captacao",
        help="Captacao de varejo dos fundos de vitrine e oportunidades de grade",
        parents=[common],
    )
    add_platform_arg(captacao_parser)
    captacao_parser.add_argument(
        "--months", type=int, default=3,
        help="Meses de informe diario a ler (default: 3)",
    )
    captacao_parser.add_argument(
        "--force", action="store_true",
        help="Forcar re-download dos informes mesmo com cache",
    )
    captacao_parser.add_argument(
        "--json", help="Tambem gravar a analise completa neste caminho (.json)"
    )
    captacao_parser.set_defaults(func=cmd_captacao)

    performance_parser = subparsers.add_parser(
        "performance",
        help="Rentabilidade da vitrine ponderada por PL e por cotistas",
        parents=[common],
    )
    add_platform_arg(performance_parser)
    performance_parser.add_argument(
        "--months", type=int, default=3,
        help="Meses de informe diario a ler (default: 3)",
    )
    performance_parser.add_argument(
        "--force", action="store_true",
        help="Forcar re-download dos informes mesmo com cache",
    )
    performance_parser.add_argument(
        "--json", help="Tambem gravar a analise completa neste caminho (.json)"
    )
    performance_parser.set_defaults(func=cmd_performance)

    rent_parser = subparsers.add_parser(
        "rentabilidade",
        help="Rentabilidade de cada fundo de vitrine em 1m, 3m, YTD, 12m e 24m",
        parents=[common],
    )
    add_platform_arg(rent_parser)
    rent_parser.add_argument(
        "--months", type=int, default=24,
        help="Competencias do informe a considerar (default: 24)",
    )
    rent_parser.add_argument(
        "--force", action="store_true",
        help="Refazer o resumo mensal mesmo com cache",
    )
    rent_parser.add_argument(
        "--json", help="Tambem gravar a analise completa neste caminho (.json)"
    )
    rent_parser.set_defaults(func=cmd_rentabilidade)

    for nome, ajuda, funcao in (
        ("custo", "Custo da vitrine relativo a categoria de cada fundo", cmd_custo),
        ("mandato", "Mandato declarado das vitrines (o que o regulamento autoriza)",
         cmd_mandato),
    ):
        parser_extra = subparsers.add_parser(nome, help=ajuda, parents=[common])
        add_platform_arg(parser_extra)
        parser_extra.add_argument(
            "--json", help="Tambem gravar a analise completa neste caminho (.json)"
        )
        parser_extra.set_defaults(func=funcao)

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

    etf_parser = subparsers.add_parser(
        "etf",
        help="Monitorar a captação semanal de todos os ETFs cadastrados na CVM",
        parents=[common],
    )
    etf_parser.add_argument(
        "--months",
        type=int,
        default=3,
        help="Quantidade de meses recentes do INF_DIARIO a analisar (default: 3)",
    )
    etf_parser.add_argument(
        "--export",
        action="store_true",
        help="Exportar resultados para planilha Excel (.xlsx) e arquivo CSV",
    )
    etf_parser.add_argument(
        "--force",
        action="store_true",
        help="Forçar re-download dos arquivos da CVM mesmo se existirem no cache",
    )
    etf_parser.add_argument(
        "-o", "--output", help="Caminho personalizado para salvar a planilha .xlsx"
    )
    etf_parser.add_argument(
        "--probe-sources",
        action="store_true",
        help=(
            "Sondar as fontes que cobrem ETF de verdade (Fundos.NET, B3 listados, "
            "COTAHIST) e gravar a evidencia bruta em data/cache/debug/etf-sources/"
        ),
    )
    etf_parser.add_argument(
        "--collect-sources",
        metavar="FONTES",
        help=(
            "Coletar a foto do dia das fontes indicadas, separadas por virgula: "
            "b3 (tickers/CNPJ), fnet (informe diario), cotahist (cotacao/liquidez)"
        ),
    )
    etf_parser.add_argument(
        "--flow-source",
        choices=["inf_diario", "fnet"],
        default="inf_diario",
        help=(
            "De onde vem o fluxo semanal: inf_diario (CVM; so cobre feeders) ou "
            "fnet (informes diarios coletados do Fundos.NET, que cobrem FIIM)"
        ),
    )
    etf_parser.set_defaults(func=cmd_etf)

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
