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
        raise ValueError(f"sem_peso deve ser 'media' ou 'none', não {sem_peso!r}")
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


# --- formatacao brasileira de dinheiro e percentual ------------------------------
# Havia uma copia destes em cada bloco (`etf_competicao`, `retail_flow._reais`,
# `digest._mm`, `dashboard._pct`), com escalas e sinais diferentes. Os daqui sao
# os do monitor de ETFs, mais o degrau de trilhao que a industria inteira pede.


def _decimal_br(valor: float, casas: int) -> str:
    """1234.5 -> "1.234,5"."""
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def reais(valor: float | None) -> str:
    """"R$ 3,95 tri", "R$ 8,6 bi", "R$ 115 mi" - sem sinal, milhar e decimal brasileiros."""
    if valor is None:
        return "-"
    if abs(valor) >= 1e12:
        return f"R$ {_decimal_br(valor / 1e12, 2)} tri"
    if abs(valor) >= 1e9:
        return f"R$ {_decimal_br(valor / 1e9, 1)} bi"
    return f"R$ {valor / 1e6:,.0f} mi".replace(",", ".")


def reais_com_sinal(valor: float | None) -> str:
    """"+R$ 1,2 bi" / "-R$ 83 mi" - para fluxo, que tem direcao."""
    if valor is None:
        return "-"
    return ("-" if valor < 0 else "+") + reais(abs(valor))


def pct(valor: float | None, casas: int = 1) -> str:
    """Fracao como "6,6%"."""
    return "-" if valor is None else f"{valor * 100:.{casas}f}%".replace(".", ",")


def pp(valor: float | None) -> str:
    """Pontos percentuais com sinal: "+0,4 pp". Zero arredondado sai sem sinal."""
    if valor is None:
        return "-"
    if abs(valor) < 0.05:
        return "0,0 pp"
    return f"{valor:+.1f} pp".replace(".", ",")
