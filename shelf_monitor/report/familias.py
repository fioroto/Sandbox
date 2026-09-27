"""Consulta por gestora: todas as familias de estrategia dela, e onde cada uma esta.

Responde a pergunta que antecede a conversa com a gestora: que estrategias ela
tem, em que versoes de liquidez, com que carteira, e qual delas a XP ja
distribui (na vitrine ou na lista distribuivel) - e qual as concorrentes
exibem. Sai no console (`python -m shelf_monitor familias "KINEA"`) e em JSON.
"""
from __future__ import annotations

from shelf_monitor.distribuivel import ROTULOS_SITUACAO, BaseDistribuivel
from shelf_monitor.familias import Familia, MapaDeFamilias, faixa_de_liquidez
from shelf_monitor.models import ShelfSnapshot
from shelf_monitor.normalize import format_cnpj, normalize_cnpj
from shelf_monitor.report.aggregate import listing_funds
from shelf_monitor.report.shared import ordem_canonica, rotulo_da_casa
from shelf_monitor.texto import competencia_br, contagem, milhar


def presenca_nas_vitrines(
    mapa: MapaDeFamilias, snapshots: list[ShelfSnapshot]
) -> dict[str, set[str]]:
    """Classe -> casas em cuja vitrine de varejo ela aparece."""
    presenca: dict[str, set[str]] = {}
    for snapshot in snapshots:
        for fundo in listing_funds(snapshot):
            for cnpj in mapa.classes_de(fundo.cnpj) or ([fundo.cnpj] if fundo.cnpj else []):
                presenca.setdefault(cnpj, set()).add(snapshot.platform)
    return presenca


def _familia_com_presenca(
    familia: Familia,
    presenca: dict[str, set[str]],
    distribuivel: BaseDistribuivel | None,
    baseline_id: str,
) -> dict:
    dados = familia.to_dict()
    na_xp = False
    for versao in dados["versoes"]:
        for membro in versao["membros"]:
            casas = ordem_canonica(presenca.get(membro["cnpj"], set()))
            membro["vitrines"] = [rotulo_da_casa(c) for c in casas]
            membro["na_vitrine_base"] = baseline_id in casas
            situacao = distribuivel.situacao_de(membro["cnpj"]) if distribuivel else None
            membro["distribuivel"] = situacao
            na_xp = na_xp or membro["na_vitrine_base"] or bool(situacao)
    dados["na_xp"] = na_xp
    return dados


def relatorio_de_familias(
    mapa: MapaDeFamilias,
    consulta: str,
    snapshots: list[ShelfSnapshot],
    distribuivel: BaseDistribuivel | None = None,
    baseline_id: str = "xp",
) -> dict:
    """As familias da gestora consultada - ou a familia do fundo, se a consulta
    for o CNPJ de um fundo."""
    presenca = presenca_nas_vitrines(mapa, snapshots)
    cnpj = normalize_cnpj(consulta) if sum(c.isdigit() for c in consulta) == 14 else None
    fundo = cnpj if cnpj and mapa.classes_de(cnpj) else None
    if fundo:
        familia = mapa.familia_de(fundo)
        familias = [familia] if familia else []
        gestoras = (
            [(familia.raiz_gestora, familia.gestora)]
            if familia and familia.raiz_gestora else []
        )
    else:
        gestoras = mapa.gestoras(consulta)
        familias = mapa.familias_da_gestora(consulta)
    itens = [
        _familia_com_presenca(f, presenca, distribuivel, baseline_id) for f in familias
    ]
    return {
        "consulta": consulta,
        "fundo": format_cnpj(fundo) if fundo else None,
        "baseline_label": rotulo_da_casa(baseline_id),
        "competencias": list(mapa.competencias),
        "gestoras": [{"raiz": r, "nome": n} for r, n in gestoras],
        "familias": itens,
        "total": len(itens),
        "na_xp": sum(1 for f in itens if f["na_xp"]),
        "lista_distribuivel": distribuivel is not None and not distribuivel.vazia,
    }


def _reais(valor: float | None) -> str:
    if not valor:
        return "-"
    if valor >= 1e9:
        return f"R$ {valor / 1e9:.1f} bi".replace(".", ",")
    if valor >= 1e6:
        return f"R$ {valor / 1e6:.0f} mi"
    return f"R$ {milhar(round(valor))}"


def print_familias(relatorio: dict) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    if not relatorio["familias"]:
        console.print(
            f"[yellow]Nenhuma família encontrada para '{relatorio['consulta']}'.[/] "
            "Consulte pelo nome (ou parte dele) da gestora, pelo CNPJ da gestora ou "
            "pelo CNPJ de um fundo."
        )
        return
    competencias = ", ".join(competencia_br(c) for c in relatorio["competencias"])
    base = relatorio["baseline_label"]
    gestoras = "; ".join(g["nome"] for g in relatorio["gestoras"]) or "-"
    console.print(
        f"[bold]{contagem(relatorio['total'], 'família', 'famílias')} de estratégia[/] "
        f"- {gestoras}. {relatorio['na_xp']} com alguma versão na {base} "
        f"(vitrine{' ou lista distribuível' if relatorio['lista_distribuivel'] else ''}). "
        f"Carteira da CVM de {competencias}."
    )
    for familia in relatorio["familias"]:
        console.print()
        marca = f"[green]na {base}[/]" if familia["na_xp"] else f"[red]fora da {base}[/]"
        console.print(
            f"[bold]{familia['nome']}[/] - {marca}\n"
            f"  {familia['anbima'] or 'sem categoria ANBIMA'}"
            f" | carteira: {familia['perfil_resumo']} | PL {_reais(familia['pl'])}"
        )
        tabela = Table(show_header=True, header_style="bold", box=None, pad_edge=False)
        tabela.add_column("Papel")
        tabela.add_column("Fundo")
        tabela.add_column("CNPJ")
        tabela.add_column("Resgate")
        tabela.add_column("PL", justify="right")
        tabela.add_column("Vitrines")
        tabela.add_column("Lista")
        for numero, versao in enumerate(familia["versoes"], start=1):
            # No console: o master e o que alguma vitrine ou a lista mostra. Os
            # espelhos de outros distribuidores (a SPX Falcon tem 46) viram uma
            # linha com as faixas de liquidez deles; o JSON traz todos.
            ocultos = []
            for membro in versao["membros"]:
                visivel = (
                    membro["papel"] == "master" or membro["vitrines"] or membro["distribuivel"]
                )
                if not visivel:
                    ocultos.append(membro)
                    continue
                dmais = membro["dmais"]
                tabela.add_row(
                    f"v{numero} {membro['papel']}",
                    (membro["nome"] or "")[:52],
                    format_cnpj(membro["cnpj"]),
                    f"D+{dmais} ({faixa_de_liquidez(dmais)})" if dmais is not None else "-",
                    _reais(membro["pl"]),
                    ", ".join(membro["vitrines"]) or "-",
                    ROTULOS_SITUACAO.get(membro["distribuivel"], "-"),
                )
            if ocultos:
                faixas = sorted(
                    {faixa_de_liquidez(m["dmais"]) for m in ocultos if m["dmais"] is not None},
                    key=_ordem_da_faixa,
                )
                tabela.add_row(
                    f"v{numero}",
                    f"+ {contagem(len(ocultos), 'espelho', 'espelhos')} fora das vitrines coletadas",
                    "",
                    "; ".join(faixas) or "-",
                    _reais(sum(m["pl"] or 0.0 for m in ocultos)),
                    "-", "-",
                )
        console.print(tabela)


def _ordem_da_faixa(rotulo: str) -> int:
    from shelf_monitor.familias import FAIXAS_LIQUIDEZ

    rotulos = [r for _, r in FAIXAS_LIQUIDEZ]
    return rotulos.index(rotulo) if rotulo in rotulos else len(rotulos)
