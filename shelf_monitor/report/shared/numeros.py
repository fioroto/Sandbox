"""Mediana, quartis e media ponderada - uma implementacao de cada.

Havia tres implementacoes de mediana (uma em `aggregate`, uma inline em
`mandato.teto_mediano`, e `statistics.median` espalhada) e cinco de media
ponderada, com fallbacks diferentes entre si quando o peso somava zero.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def mediana(valores: Iterable[float]) -> float | None:
    """Mediana, ou None quando nao ha valor nenhum."""
    ordenados = sorted(valores)
    if not ordenados:
        return None
    meio = len(ordenados) // 2
    if len(ordenados) % 2:
        return ordenados[meio]
    return (ordenados[meio - 1] + ordenados[meio]) / 2


def quartis(valores: Iterable[float]) -> dict | None:
    """q1/mediana/q3 - quartis dizem a forma da prateleira; media esconde."""
    ordenados = sorted(valores)
    if len(ordenados) < 4:
        return None
    meio = len(ordenados) // 2
    inferior = ordenados[:meio]
    superior = ordenados[meio + 1:] if len(ordenados) % 2 else ordenados[meio:]
    return {
        "q1": round(mediana(inferior), 4),
        "median": round(mediana(ordenados), 4),
        "q3": round(mediana(superior), 4),
        "n": len(ordenados),
    }


def ponderada(
    itens: Iterable[T],
    valor: Callable[[T], float | None],
    peso: Callable[[T], float | None],
    *,
    sem_peso: str = "media",
) -> float | None:
    """Media de `valor` ponderada por `peso`.

    `sem_peso` decide o que fazer quando o peso total da zero, e e explicito
    de proposito: as cinco implementacoes que esta funcao substituiu
    divergiam exatamente ai, e a escolha muda numero publicado.

    - `"media"`: cai para a media simples. Peso zero e "nao sei o tamanho",
      nao "nao existe" - e a leitura de `agrupar_por_classe` e da serie
      mensal, onde o retorno da subclasse existe mesmo com PL zerado.
    - `"none"`: devolve None. E a leitura da rentabilidade ponderada, onde o
      numero e "quanto rendeu o real medio": sem real nenhum, nao ha real
      medio, e uma media simples responderia outra pergunta.

    Em ambos, item sem valor e ignorado, e peso negativo conta como zero.
    """
    if sem_peso not in ("media", "none"):
        raise ValueError(f"sem_peso deve ser 'media' ou 'none', nao {sem_peso!r}")
    pares = [
        (v, max(peso(item) or 0.0, 0.0))
        for item in itens
        if (v := valor(item)) is not None
    ]
    if not pares:
        return None
    total = sum(p for _, p in pares)
    if total > 0:
        return sum(v * p for v, p in pares) / total
    if sem_peso == "none":
        return None
    return sum(v for v, _ in pares) / len(pares)


def arredonda(valor: float | None, casas: int = 4) -> float | None:
    """Arredonda preservando None - o padrao dos `to_dict` dos blocos."""
    return None if valor is None else round(valor, casas)
