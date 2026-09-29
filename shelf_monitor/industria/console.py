"""O bloco da industria no console: funil, camadas e dono do dinheiro."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from shelf_monitor.industria.universo import UNIVERSO
from shelf_monitor.report.shared.numeros import pct, reais
from shelf_monitor.texto import data_br, milhar


def _bi(valor: float) -> str:
    return f"{valor / 1e9:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def imprimir_bloco(bloco: dict, console: Console | None = None) -> None:
    console = console or Console()
    if not bloco.get("available"):
        console.print(f"[yellow]Indústria indisponível:[/] {bloco.get('reason')}")
        for aviso in bloco.get("avisos") or ():
            console.print(f"  [dim]{aviso}[/]")
        return

    informe = bloco["informe"]
    console.print()
    console.print(
        f"[bold]Indústria de fundos — {bloco['rotulo_competencia']}[/] "
        f"[dim](informe diário de {data_br(informe['de'])} a {data_br(informe['ate'])}; "
        f"cadastro de {bloco['qualidade']['retrato_do_cadastro'][4:]}/"
        f"{bloco['qualidade']['retrato_do_cadastro'][:4]})[/]"
    )
    for frase in bloco.get("leitura") or ():
        console.print("  " + frase.replace("**", ""))

    tabela = Table(title="Funil do universo", title_style="bold")
    tabela.add_column("Degrau")
    tabela.add_column("Produtos", justify="right")
    tabela.add_column("Classes", justify="right")
    tabela.add_column("PL (R$ bi)", justify="right")
    for degrau in bloco["funil"]:
        estilo = "bold" if degrau["motivo"] == UNIVERSO else ""
        tabela.add_row(
            degrau["rotulo"], milhar(degrau["produtos"]), milhar(degrau["classes"]),
            _bi(degrau["pl"]), style=estilo,
        )
    console.print()
    console.print(tabela)
    console.print(
        "  [dim]PL do informe diário no fim do mês; estruturados e ETF, que não "
        "entregam o informe, com o PL do cadastro.[/]"
    )

    camadas = bloco["camadas"]
    total = camadas["pl_bruto"] - camadas["sem_perfil"]
    tabela = Table(title="De quem é o dinheiro do universo", title_style="bold")
    tabela.add_column("Segmento")
    tabela.add_column("PL (R$ bi)", justify="right")
    tabela.add_column("Fatia", justify="right")
    for segmento in bloco["segmentos"]:
        tabela.add_row(
            segmento["rotulo"], _bi(segmento["pl"]),
            pct(segmento["pl"] / total) if total else "-",
        )
    console.print()
    console.print(tabela)
    console.print(
        f"  Camada do investidor: [bold]{reais(camadas['pl_investidor'])}[/] de "
        f"{reais(camadas['pl_bruto'])} brutos; sem perfil no mês: {reais(camadas['sem_perfil'])}."
    )

    adjacentes = bloco.get("adjacentes") or {}
    if adjacentes:
        partes = [
            f"{item['rotulo']}: {milhar(item['produtos'])} produtos, {reais(item['pl'])}"
            + (" (PL do cadastro)" if item.get("fonte_do_pl") == "cadastro" else "")
            for item in adjacentes.values()
        ]
        console.print("  Camadas ao lado (fora da soma): " + "; ".join(partes) + ".")

    qualidade = bloco.get("qualidade") or {}
    cobertura = qualidade.get("cobertura_perfil")
    if cobertura is not None:
        console.print(f"  [dim]Perfil do próprio mês cobre {pct(cobertura)} do PL do universo.[/]")
    for aviso in bloco.get("avisos") or ():
        console.print(f"  [yellow]{aviso}[/]")
