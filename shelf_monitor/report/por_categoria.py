"""Captacao por categoria das vitrines do digest: a visao de mercado.

Pedidos de 17 e 19/10/2026. Todos os fundos das vitrines em foco (XP, BTG e
Itau), cada um uma vez, com a captacao do fundo no mercado inteiro - o informe
diario da CVM e por fundo, nao por distribuidor -, somados pela categoria
ANBIMA, a mesma da coluna Categoria das listas. O recorte e o do ritmo por
casa: com informe, PL de ao menos R$ 1 mi no inicio e sem movimento
estrutural; caixa/simples e previdencia ja sairam da captacao
(`shelf_monitor.caixa`, `shelf_monitor.previdencia_fora`). O ritmo e o da soma
de cada categoria, pela regra dos fundos: a captacao por dia da semana contra a
das 12 semanas anteriores.

A primeira versao (17/10/2026) somava so os gaps reais de BTG e Itau, na secao
de oportunidade; a pedido, virou a visao de mercado das vitrines, na secao de
captacao. Medido no relatorio de 14/10/2026: 1.113 fundos em 30 categorias.
"""
from __future__ import annotations

from shelf_monitor.models import ShelfSnapshot
from shelf_monitor.report.aggregate import anbima_category, listing_funds
from shelf_monitor.report.retail_flow import (
    _MIN_PL_RANKING,
    FundFlow,
    SemanaDoFundo,
    ritmo_da_semana,
)


def captacao_por_categoria(
    snapshots: list[ShelfSnapshot], flow: dict | None, foco: set[str]
) -> list[dict]:
    """A captacao das vitrines em foco por categoria, da maior para a menor.

    Sem os objetos do informe (`_fluxos`, bloco lido de JSON) ou sem informe
    nesta execucao, nao ha tabela.
    """
    fluxos = (flow or {}).get("_fluxos") if (flow or {}).get("available") else None
    if not fluxos:
        return []
    categorias: dict[str, str] = {}
    for snapshot in snapshots:
        if snapshot.platform not in foco:
            continue
        for oferta in listing_funds(snapshot):
            if oferta.cnpj:
                categorias.setdefault(oferta.cnpj, anbima_category(oferta))

    grupos: dict[str, list[FundFlow]] = {}
    for fluxo in fluxos:
        if not foco & set(fluxo.vitrines):
            continue
        if fluxo.estrutural or fluxo.pl_inicio < _MIN_PL_RANKING:
            continue
        categoria = categorias.get(fluxo.cnpj) or "Sem classificação"
        grupos.setdefault(categoria, []).append(fluxo)

    recorte = flow.get("_recorte")
    com_semana = bool(recorte is not None and recorte.todas())
    linhas = [
        {
            "categoria": categoria,
            "fundos": len(do_grupo),
            "captacao_3m": round(sum(f.captacao_liquida for f in do_grupo), 2),
            "captacao_semana": (
                round(sum(f.captacao_semana or 0.0 for f in do_grupo), 2) if com_semana else None
            ),
            "ritmo_semanal": _ritmo_da_soma(do_grupo) if com_semana else None,
        }
        for categoria, do_grupo in grupos.items()
    ]
    return sorted(linhas, key=lambda linha: -linha["captacao_3m"])


def _ritmo_da_soma(fluxos: list[FundFlow]) -> str:
    """O ritmo da semana da soma dos fundos: as series semanais estao alinhadas
    as mesmas semanas do recorte (`weekly_by_fund`), entao somam posicao a posicao."""
    series = [f.semanas for f in fluxos if f.semanas]
    if not series:
        return "sem_serie"
    soma = []
    for i, semana in enumerate(series[0]):
        da_semana = [serie[i] for serie in series if i < len(serie)]
        soma.append(SemanaDoFundo(
            semana.segunda,
            sum(s.captacao_liquida for s in da_semana),
            max(s.dias for s in da_semana),
        ))
    return ritmo_da_semana(soma)
