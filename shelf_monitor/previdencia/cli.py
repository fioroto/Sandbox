"""CLI do monitor de previdencia.

Uso:
    python -m shelf_monitor.previdencia run          # coleta, diff e relatorios
    python -m shelf_monitor.previdencia collect      # so a coleta
    python -m shelf_monitor.previdencia report       # re-renderiza dos snapshots
    python -m shelf_monitor.previdencia insights     # leitura competitiva, sem coletar

A orquestracao e a do modulo de fundos (`shelf_monitor.main`): mesmas funcoes de
coleta, relatorio e renderizacao, chamadas com o `ConfigPrevidencia`, o registro
de coletores daqui, os tres ganchos de `build_report` (resolvedor por nome,
ajuste pos-enriquecimento e indice de custo) e o dashboard proprio, no molde do
de fundos (`shelf_monitor.previdencia.dashboard`). Nao ha radar CVM: ele
acompanha registros de fundos das gestoras da watchlist, e continua no `run` de
fundos.
"""
from __future__ import annotations

import argparse
import sys

from shelf_monitor.enrich import enrich_snapshots
from shelf_monitor.main import (
    build_report,
    carregar_snapshots,
    collect_platforms,
    console,
    ensure_market_base,
    git_commit,
    print_summary,
    render_outputs,
    run_outcome,
    warn_market_missing,
)
from shelf_monitor.models import RunReport, ShelfSnapshot
from shelf_monitor.previdencia.coletores import construir_coletor
from shelf_monitor.previdencia.config import (
    PLATAFORMAS,
    ROTULOS_PROPRIOS,
    ConfigPrevidencia,
)
from shelf_monitor.previdencia.custo import IndiceCustoPrevidencia
from shelf_monitor.previdencia.dashboard import render_dashboard_previdencia
from shelf_monitor.previdencia.eixo import IndicePrevidencia, ajustar_previdencia
from shelf_monitor.report.competitive import (
    build_competitive_view,
    print_competitive_view,
)
from shelf_monitor.report.shared import rotulos_sobrepostos


def _ajustar(snapshots: list[ShelfSnapshot]) -> None:
    """O gancho `pos_enriquecimento`, com uma linha no console do que mudou."""
    contagem = ajustar_previdencia(snapshots)
    if contagem:
        partes = ", ".join(f"{k.replace('_', ' ')}: {v}" for k, v in sorted(contagem.items()))
        console.print(f"[dim]Ajuste de previdência - {partes}[/]")


def construir_relatorio(
    config: ConfigPrevidencia,
    snapshots: list[ShelfSnapshot],
    *,
    with_rentabilidade: bool = False,
) -> RunReport:
    """`build_report` com resolvedor, ajuste e custo de previdencia, sem radar CVM."""
    return build_report(
        config,
        snapshots,
        None,
        with_rentabilidade=with_rentabilidade,
        classificador=IndicePrevidencia,
        pos_enriquecimento=_ajustar,
        indice_custo=IndiceCustoPrevidencia,
    )


def renderizar(
    config: ConfigPrevidencia, report: RunReport, *, send_email: bool = False
) -> list[str]:
    """`render_outputs` com o dashboard da previdencia; devolve as falhas."""
    return render_outputs(
        config, report, send_email=send_email,
        renderizar_dashboard=render_dashboard_previdencia,
    )


def cmd_run(config: ConfigPrevidencia, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    snapshots = collect_platforms(
        config, args.platforms or list(PLATAFORMAS), construtor=construir_coletor
    )
    if not args.no_market:
        ensure_market_base(config)

    report = construir_relatorio(
        config, snapshots, with_rentabilidade=args.with_rentabilidade
    )
    print_summary(report)
    failures = renderizar(config, report, send_email=args.send_email)

    if args.git_commit:
        git_commit(config)

    line, exit_code = run_outcome(report, False, failures)
    console.print(f"[{'red' if exit_code else 'green'}]{line}[/]")
    return exit_code


def cmd_collect(config: ConfigPrevidencia, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    snapshots = collect_platforms(
        config, args.platforms or list(PLATAFORMAS), construtor=construir_coletor
    )
    print_summary(construir_relatorio(config, snapshots))
    return 0


def cmd_report(config: ConfigPrevidencia, args: argparse.Namespace) -> int:
    config.ensure_dirs()
    snapshots = carregar_snapshots(config, args.platforms or list(PLATAFORMAS))
    if not snapshots:
        console.print(
            "[yellow]Nenhum snapshot de previdência. Rode "
            "`python -m shelf_monitor.previdencia run` antes.[/]"
        )
        return 1
    report = construir_relatorio(
        config, snapshots, with_rentabilidade=args.with_rentabilidade
    )
    print_summary(report)
    failures = renderizar(config, report, send_email=args.send_email)
    return 1 if failures else 0


def cmd_insights(config: ConfigPrevidencia, args: argparse.Namespace) -> int:
    """Leitura competitiva das vitrines gravadas - o eixo grosso ja por familia."""
    snapshots = carregar_snapshots(config, args.platforms or list(PLATAFORMAS))
    if not snapshots:
        console.print(
            "[yellow]Nenhum snapshot de previdência. Rode "
            "`python -m shelf_monitor.previdencia run` antes.[/]"
        )
        return 1
    if enrich_snapshots(config, snapshots, classificador=IndicePrevidencia) is None:
        warn_market_missing()
    _ajustar(snapshots)
    print_competitive_view(build_competitive_view(config, snapshots))
    return 0


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m shelf_monitor.previdencia",
        description="Monitor de previdência - vitrines públicas de PGBL/VGBL.",
    )
    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument("--data-dir", help="Diretório de dados (padrão: data/)")
    comum.add_argument(
        "--platform",
        dest="platforms",
        action="append",
        choices=list(PLATAFORMAS),
        help="Casa a coletar (repetível). Padrão: todas.",
    )

    relatorio = argparse.ArgumentParser(add_help=False)
    relatorio.add_argument(
        "--send-email", action="store_true", help="Enviar o digest por SMTP"
    )
    relatorio.add_argument(
        "--with-rentabilidade",
        action="store_true",
        help="Anexar a rentabilidade por janela (lê até 24 competências do informe)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser(
        "run", help="Coleta completa + relatórios", parents=[comum, relatorio]
    )
    run.add_argument(
        "--no-market",
        action="store_true",
        help="Não importar a base de mercado quando ela estiver ausente",
    )
    run.add_argument(
        "--git-commit",
        action="store_true",
        help="Commitar data/previdencia/ ao final",
    )
    run.set_defaults(func=cmd_run)

    collect = sub.add_parser("collect", help="Só a coleta", parents=[comum])
    collect.set_defaults(func=cmd_collect)

    report = sub.add_parser(
        "report", help="Re-renderizar dos snapshots", parents=[comum, relatorio]
    )
    report.set_defaults(func=cmd_report)

    insights = sub.add_parser(
        "insights", help="Leitura competitiva dos snapshots, sem coletar", parents=[comum]
    )
    insights.set_defaults(func=cmd_insights)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = criar_parser().parse_args(argv)
    config = ConfigPrevidencia(data_dir=args.data_dir)
    errors = config.validate()
    if errors:
        for error in errors:
            console.print(f"[red]{error}[/]")
        return 2

    console.print(f"[bold]{config.titulo_relatorio}[/]")
    # Os rotulos proprios (o Itau Private) valem so durante esta execucao.
    with rotulos_sobrepostos(ROTULOS_PROPRIOS):
        return args.func(config, args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
