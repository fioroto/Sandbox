"""Os fundos entregam? Rentabilidade de 12 e 3 meses por peer group (N2).

Uma estrategia conta uma vez: o retorno e o da RAIZ (o master onde a carteira
mora), nao o de cada veiculo. Sem isso, um master com trinta feeders e
espelhos pesaria trinta vezes na mediana do peer group. Quando a raiz nao tem
serie (master fora do informe), vale o retorno do proprio veiculo.

O retorno vem de `SerieMensal.retorno`: base no fechamento da competencia
anterior a janela (convencao de lamina), subclasses ponderadas pelo PL, e a
classe precisa aparecer em ao menos 80% das competencias. Nao e retorno total
(a cota e ex-provento) nem e liquido de impostos.
"""
from __future__ import annotations

from shelf_monitor.industria.agregados import dimensoes
from shelf_monitor.industria.base import BaseIndustria
from shelf_monitor.industria.janelas import Janela
from shelf_monitor.industria.painel import linhas_do_mes
from shelf_monitor.industria.universo import UNIVERSO
from shelf_monitor.report.shared.limiares import (
    MAX_RETORNO_JANELA_PLAUSIVEL,
    MIN_AMOSTRA_CATEGORIA,
    MIN_RETORNO_JANELA_PLAUSIVEL,
)
from shelf_monitor.report.shared.numeros import quartis


def _quartil(valor: float, faixas: dict) -> int:
    """1 = quartil de cima (melhor retorno), 4 = de baixo."""
    if valor >= faixas["q3"]:
        return 1
    if valor >= faixas["median"]:
        return 2
    if valor >= faixas["q1"]:
        return 3
    return 4


def rentabilidade_por_peer_group(base: BaseIndustria, janela: Janela) -> dict:
    """{"n2": [...], "por_classe": {cnpj: {retorno, quartil, raiz}}} na janela."""
    competencias = list(janela.competencias)
    if not competencias:
        return {"n2": [], "por_classe": {}}
    ultima = competencias[-1]
    classes_no_universo: dict[str, tuple[str, str, str]] = {}
    for linha in linhas_do_mes(base, ultima):
        if linha.motivo != UNIVERSO:
            continue
        dim = dimensoes(base, linha)
        classes_no_universo.setdefault(linha.classe, (dim.n1, dim.n2, dim.raiz))

    retorno_da_classe: dict[str, float | None] = {}

    def retorno(cnpj: str) -> float | None:
        if cnpj not in retorno_da_classe:
            resultado = base.serie.retorno(cnpj, competencias)
            valor = resultado.retorno / 100 if resultado else None
            if valor is not None and not (
                MIN_RETORNO_JANELA_PLAUSIVEL <= valor <= MAX_RETORNO_JANELA_PLAUSIVEL
            ):
                valor = None  # evento na cota, nao desempenho
            retorno_da_classe[cnpj] = valor
        return retorno_da_classe[cnpj]

    por_raiz: dict[tuple[str, str], dict[str, float]] = {}
    por_classe: dict[str, dict] = {}
    for cnpj, (n1, n2, raiz) in classes_no_universo.items():
        valor = retorno(raiz) if raiz != cnpj else None
        fonte = "raiz"
        if valor is None:
            valor, fonte = retorno(cnpj), "propria"
        if valor is None:
            continue
        por_raiz.setdefault((n1, n2), {})[raiz if fonte == "raiz" else cnpj] = valor
        por_classe[cnpj] = {"retorno": round(valor, 6), "fonte": fonte, "raiz": raiz}

    grupos = []
    for (n1, n2), valores in sorted(por_raiz.items()):
        lista = list(valores.values())
        faixas = quartis(lista)
        grupos.append({
            "n1": n1,
            "n2": n2,
            "estrategias": len(lista),
            "amostra_pequena": len(lista) < MIN_AMOSTRA_CATEGORIA,
            "q1": faixas["q1"] if faixas else None,
            "mediana": faixas["median"] if faixas else (sorted(lista)[len(lista) // 2] if lista else None),
            "q3": faixas["q3"] if faixas else None,
            "_faixas": faixas,
        })
    faixas_por_n2 = {(g["n1"], g["n2"]): g["_faixas"] for g in grupos}
    for cnpj, item in por_classe.items():
        n1, n2, _ = classes_no_universo[cnpj]
        faixas = faixas_por_n2.get((n1, n2))
        item["quartil"] = _quartil(item["retorno"], faixas) if faixas else None
    for grupo in grupos:
        grupo.pop("_faixas")
    return {"n2": grupos, "por_classe": por_classe}
