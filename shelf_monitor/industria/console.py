"""O bloco da industria no console: funil, camadas e dono do dinheiro."""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

from shelf_monitor.industria.curadoria import TIPOS_DE_GESTORA
from shelf_monitor.industria.universo import UNIVERSO
from shelf_monitor.report.shared.numeros import pct, reais, reais_com_sinal
from shelf_monitor.texto import data_br, milhar


def _bi(valor: float) -> str:
    return f"{valor / 1e9:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _tabela_simples(console: Console, titulo: str, colunas: tuple[str, ...], linhas: list) -> None:
    if not linhas:
        return
    tabela = Table(title=titulo, title_style="bold")
    for i, coluna in enumerate(colunas):
        tabela.add_column(coluna, justify="left" if i == 0 else "right")
    for linha in linhas:
        tabela.add_row(*linha)
    console.print()
    console.print(tabela)


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

    for prazo in bloco.get("prazos") or ():
        console.print(f"  [dim]Prazo — {prazo}[/]")

    def fluxo(item: dict, janela: str) -> tuple[str, str]:
        dados = (item.get("janelas") or {}).get(janela) or {}
        if not dados:
            return "-", "-"
        return reais_com_sinal(dados.get("fluxo_investidor")), pct(dados.get("sobre_pl"))

    _tabela_simples(
        console, "Estratégias do universo (camada do investidor)",
        ("Macroclasse", "Classes", "PL (R$ bi)", "Fatia", "Captação 12m", "% PL"),
        [(i["n1"], milhar(i["classes"]), _bi(i["pl_investidor"]), pct(i.get("fatia")), *fluxo(i, "12m"))
         for i in bloco.get("estrategias", {}).get("n1", [])],
    )
    n2 = sorted(
        (i for i in bloco.get("estrategias", {}).get("n2", []) if (i.get("janelas") or {}).get("12m")),
        key=lambda i: -i["janelas"]["12m"]["fluxo_investidor"],
    )
    _tabela_simples(
        console, "Peer groups que mais captam e mais perdem em 12 meses",
        ("Peer group", "Macroclasse", "PL (R$ bi)", "Captação 12m", "% PL", "Mediana 12m"),
        [(i["n2"], i["n1"], _bi(i["pl_investidor"]), *fluxo(i, "12m"),
          pct((i.get("rentabilidade") or {}).get("12m", {}).get("mediana")))
         for i in n2[:8] + n2[-5:]],
    )
    familias = bloco.get("familias") or {}
    _tabela_simples(
        console, "Estratégias (famílias) que mais captam em 12 meses",
        ("Estratégia", "Gestora", "Peer group", "Veículos", "Captação 12m"),
        [(i["nome"][:48], i["gestora"][:22], i["n2"][:28], milhar(i["veiculos"]),
          reais_com_sinal(i["fluxo_investidor"]))
         for i in (familias.get("que_mais_captam") or [])[:10]],
    )
    _tabela_simples(
        console, "Estratégias (famílias) que mais perdem em 12 meses",
        ("Estratégia", "Gestora", "Peer group", "Veículos", "Captação 12m"),
        [(i["nome"][:48], i["gestora"][:22], i["n2"][:28], milhar(i["veiculos"]),
          reais_com_sinal(i["fluxo_investidor"]))
         for i in (familias.get("que_mais_perdem") or [])[:10]],
    )
    _tabela_simples(
        console, "Papel na estrutura",
        ("Papel", "Produtos", "PL (R$ bi)", "Fatia"),
        [(i["rotulo"], milhar(i["produtos"]), _bi(i["pl_investidor"]), pct(i.get("fatia")))
         for i in bloco.get("estrutura", {}).get("papeis", [])],
    )
    _tabela_simples(
        console, "Casas dedicadas (espelhos e feeders feitos para um distribuidor)",
        ("Casa", "Veículos", "PL (R$ bi)"),
        [(i["casa"], milhar(i["produtos"]), _bi(i["pl_investidor"]))
         for i in bloco.get("estrutura", {}).get("casas_dedicadas", [])[:10]],
    )
    gestoras = bloco.get("gestoras", {})
    _tabela_simples(
        console, "Maiores grupos gestores (gestora da estratégia)",
        ("Grupo", "Tipo", "PL (R$ bi)", "Fatia"),
        [(i["grupo"], TIPOS_DE_GESTORA.get(i["tipo"], i["tipo"]), _bi(i["pl_investidor"]), pct(i.get("fatia")))
         for i in gestoras.get("grupos", [])[:15]],
    )
    if gestoras.get("cobertura_curada") is not None:
        console.print(
            f"  [dim]{milhar(gestoras['grupos_no_universo'])} grupos no universo; grupo "
            f"curado cobre {pct(gestoras['cobertura_curada'])} do PL.[/]"
        )

    adjacentes = bloco.get("adjacentes") or {}
    for item in adjacentes.values():
        if item.get("available") is False:
            console.print(f"  [dim]{item['rotulo']} (camada ao lado): {item.get('reason')}[/]")
            continue
        doze = (item.get("janelas") or {}).get("12m") or {}
        console.print(
            f"  {item['rotulo']} (camada ao lado, fora da soma): "
            f"{reais(item.get('pl_investidor', item.get('pl')))}; captação 12m "
            f"{reais_com_sinal(doze.get('fluxo_investidor'))}."
        )
    lancamentos = (bloco.get("lancamentos") or {}).get("12m") or {}
    if lancamentos:
        console.print(
            f"  Em 12 meses entraram {milhar(lancamentos['entrou']['produtos'])} produtos no universo "
            f"({reais(lancamentos['entrou']['pl_investidor'])} hoje) e saíram "
            f"{milhar(lancamentos['saiu']['produtos'])}."
        )

    qualidade = bloco.get("qualidade") or {}
    cobertura = qualidade.get("cobertura_perfil")
    if cobertura is not None:
        console.print(f"  [dim]Perfil do próprio mês cobre {pct(cobertura)} do PL do universo.[/]")
    for aviso in bloco.get("avisos") or ():
        console.print(f"  [yellow]{aviso}[/]")
