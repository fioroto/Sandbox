"""Subcomando `industria` do CLI: `python -m shelf_monitor industria`.

Registrado por `main.py` como o `etf-bruto` (`registrar_subcomando`). O custo
de cada opcao vai no texto de ajuda, como no resto do CLI.

Dois ciclos: o mensal (universo, bloco, painel, planilha, e-mail e o retrato
do universo para o flash) e o flash semanal (captacao da semana por
macroclasse). `--ciclo auto`, o padrao, escolhe - e e o que o
`run_monitor.bat` chama toda vez.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from rich.console import Console

from shelf_monitor.config import Config
from shelf_monitor.industria.base import MESES_PADRAO, carregar_base
from shelf_monitor.industria.bloco import montar_bloco
from shelf_monitor.industria.ciclo import (
    decidir_ciclo,
    escolher_competencia,
    garantir_retrato,
    normalizar_competencia,
)
from shelf_monitor.industria.console import imprimir_bloco
from shelf_monitor.industria.flash import gravar_retrato_do_universo, montar_flash
from shelf_monitor.report.industria_email import gravar_email, gravar_flash
from shelf_monitor.report.industria_painel import render_painel_industria
from shelf_monitor.report.industria_planilha import gravar_planilha

console = Console()

CICLOS = ("auto", "mensal", "flash")


def registrar_subcomando(subparsers, common: argparse.ArgumentParser) -> None:
    parser = subparsers.add_parser(
        "industria",
        help=(
            "Monitor da indústria de fundos: universo disponível para PF/PJ, camada do "
            "investidor e de quem é o dinheiro, sem casa em foco"
        ),
        parents=[common],
    )
    parser.add_argument(
        "--ciclo",
        choices=CICLOS,
        default="auto",
        help=(
            "auto (padrão): o mensal quando o perfil e a carteira da competência já saíram e ela "
            "ainda não foi publicada; senão o flash, se o último tem 7 dias ou mais; senão nada. "
            "mensal: universo, painel, planilha e e-mail (~1 min com cache; 8 a 10 min na "
            "primeira vez). flash: a captação da semana por macroclasse (~30 s; lê o informe "
            "diário das últimas 13 semanas, ~11 MB por mês que faltar em disco)"
        ),
    )
    parser.add_argument(
        "--competencia",
        metavar="AAAA-MM",
        help=(
            "Competência de referência do ciclo mensal (padrão: a última fechada com perfil "
            "mensal publicado pela CVM, que sai por volta do dia 15). Implica --ciclo mensal"
        ),
    )
    parser.add_argument(
        "--meses",
        type=int,
        default=MESES_PADRAO,
        help=(
            f"Tamanho da janela, em competências (padrão {MESES_PADRAO}: doze meses e a "
            "competência-base). 25 dá dois anos, ao custo de ~13 MB de perfil mensal por "
            "mês a mais na primeira vez"
        ),
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Não acessar a rede: só o que já está em disco (base de mercado, perfis e informe)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Reimportar a base de mercado e rebaixar o perfil das competências recentes "
            "mesmo sem mudança na origem"
        ),
    )
    parser.add_argument(
        "--json",
        metavar="ARQUIVO",
        help="Gravar o bloco do ciclo (o mensal ou o flash) em JSON",
    )
    parser.set_defaults(func=cmd_industria)


def _progresso(etapa: str, detalhe: str) -> None:
    console.print(f"  [dim]{etapa}: {detalhe}[/]")


def publicar(config: Config, bloco: dict) -> dict[str, Path]:
    """Grava as saidas do ciclo mensal e a trilha de auditoria do bloco.

    Cada saida e isolada: uma que falhe nao impede as outras, e a falha sai no
    console com o motivo.
    """
    gravados: dict[str, Path] = {}
    for nome, gravar in (
        ("historico", lambda: gravar_historico(config, bloco)),
        ("planilha", lambda: gravar_planilha(config, bloco)),
        ("painel", lambda: render_painel_industria(config, bloco)),
        ("e-mail", lambda: gravar_email(config, bloco)),
        ("universo do flash", lambda: gravar_retrato_do_universo(config, bloco)),
    ):
        try:
            gravados[nome] = gravar()
        except Exception as exc:  # noqa: BLE001 - uma saida nao derruba as outras
            console.print(f"  [yellow]{nome} não gravado: {exc}[/]")
    console.print()
    for nome, caminho in gravados.items():
        console.print(f"  [green]{nome}:[/] {caminho}")
    return gravados


def gravar_historico(config: Config, bloco: dict) -> Path:
    """O bloco publicado da competencia, sem as chaves privadas: a trilha de
    auditoria em data/historico/industria/AAAAMM.json (versionada)."""
    destino = config.historico_dir / "industria" / f"{bloco['competencia']}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    limpo = {k: v for k, v in bloco.items() if not k.startswith("_")}
    destino.write_text(
        json.dumps(limpo, indent=1, ensure_ascii=False, default=str, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destino


def _gravar_json(caminho: str, dados: dict) -> None:
    destino = Path(caminho)
    destino.parent.mkdir(parents=True, exist_ok=True)
    limpo = {k: v for k, v in dados.items() if not k.startswith("_")}
    destino.write_text(json.dumps(limpo, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    console.print(f"[green]Bloco em JSON:[/] {destino}")


def _ciclo_mensal(config: Config, args: argparse.Namespace, pedida: str | None, baixar: bool) -> int | None:
    retrato, avisos = garantir_retrato(config, baixar=baixar, force=args.force)
    for aviso in avisos:
        console.print(f"  [yellow]{aviso}[/]")
    if retrato is None:
        console.print("[red]Sem retrato do cadastro: rode `python -m shelf_monitor market`.[/]")
        return 1

    competencia, aviso = escolher_competencia(config, pedida, baixar=baixar)
    if aviso:
        console.print(f"  [yellow]{aviso}[/]")
    console.print(f"  competência {competencia[4:]}/{competencia[:4]}, janela de {args.meses} meses")

    base = carregar_base(
        config, competencia, meses=args.meses, baixar=baixar, force=args.force,
        progresso=_progresso,
    )
    if base is None:
        console.print("[red]Sem cadastro para montar o universo.[/]")
        return 1
    bloco = montar_bloco(base, config)
    imprimir_bloco(bloco, console)
    if bloco.get("available"):
        publicar(config, bloco)
    if args.json:
        _gravar_json(args.json, bloco)
    return None if bloco.get("available") else 1


def _ciclo_flash(config: Config, args: argparse.Namespace, baixar: bool) -> int | None:
    flash = montar_flash(config, baixar=baixar)
    if args.json:
        _gravar_json(args.json, flash)
    if not flash.get("available"):
        console.print(f"[yellow]Flash não gerado: {flash.get('reason')}[/]")
        return 1
    for frase in flash["leitura"]:
        console.print(f"  {re.sub(r'[*]{2}', '', frase)}")
    html = gravar_flash(config, flash)
    console.print(f"\n  [green]flash:[/] {html.with_suffix('.md')}\n  [green]flash:[/] {html}")
    return None


def cmd_industria(config: Config, args: argparse.Namespace) -> int | None:
    config.ensure_dirs()
    baixar = not args.offline
    try:
        pedida = normalizar_competencia(args.competencia)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        return 2

    console.print("[bold]Monitor da indústria de fundos[/]")
    ciclo = "mensal" if pedida else args.ciclo
    if ciclo == "auto":
        ciclo, motivo = decidir_ciclo(config, baixar=baixar)
        console.print(f"  ciclo automático: [bold]{ciclo}[/] ({motivo})")
        if ciclo == "nada":
            return None
    if ciclo == "flash":
        return _ciclo_flash(config, args, baixar)
    return _ciclo_mensal(config, args, pedida, baixar)
