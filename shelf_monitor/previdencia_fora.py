"""Fundo de previdencia fora das analises do monitor de fundos.

Pedido de 17/10/2026: a previdencia tem o monitor proprio (shelf_monitor.previdencia),
e o fundo de previdencia que aparece numa vitrine de fundos sai da captacao,
dos gaps, da rentabilidade e do custo do monitor de fundos - como o caixa
(`shelf_monitor.caixa`), cada bloco dizendo quantos tirou (`previdencia_fora`).

A regra: o rotulo ANBIMA comeca por "Previdencia" (o cadastro classifica assim o
fundo exclusivo de previdencia, FIE ou FIF), ou o nome traz a marca - Prev,
Previdencia, FIE, VGBL, PGBL. Medido nas vitrines de 14/10/2026: 5 fundos, todos
pelo rotulo (3 na XP e 3 no BTG, um nas duas), com +R$ 30 mi em 3 meses; o
nome nao achou nenhum a mais, e fica para o fundo sem categoria.

O monitor de previdencia passa pelos mesmos blocos e analisa justamente esses
fundos: `Config.tira_previdencia` liga a regra, e o ConfigPrevidencia a desliga.
"""
from __future__ import annotations

import re

from shelf_monitor.caixa import sem_os
from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import normalize_text
from shelf_monitor.texto import contagem

# A marca de previdencia no nome, ja normalizado.
_NOME_DE_PREVIDENCIA = re.compile(r"\bPREV\b|\bPREVIDENCIA\b|\bFIE\b|\bVGBL\b|\bPGBL\b")


def e_previdencia(nome: str | None, categoria: str | None) -> bool:
    """Fundo de previdencia: rotulo ANBIMA de previdencia ou a marca no nome."""
    if normalize_text(categoria).startswith("PREVIDENCIA"):
        return True
    return bool(_NOME_DE_PREVIDENCIA.search(normalize_text(nome)))


def oferta_previdencia(fund: FundOffer) -> bool:
    """`e_previdencia` de um fundo de vitrine, com o rotulo das fontes do caixa."""
    categoria = fund.extra.get(KEY_CATEGORIA) or fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM)
    return e_previdencia(fund.name, categoria)


def sem_previdencia(
    vitrines: dict[str, list[FundOffer]], config=None
) -> tuple[dict[str, list[FundOffer]], int]:
    """As vitrines sem os fundos de previdencia, e quantos fundos distintos sairam.

    So no monitor de fundos: com o config da previdencia (`tira_previdencia`
    falso), as vitrines voltam inteiras.
    """
    if config is not None and not getattr(config, "tira_previdencia", True):
        return vitrines, 0
    return sem_os(vitrines, oferta_previdencia)


def nota_previdencia(fora: int | None) -> str | None:
    """A frase que toda superficie usa para dizer quantos fundos de previdencia sairam."""
    if not fora:
        return None
    return (
        f"{contagem(fora, 'fundo de previdência fica', 'fundos de previdência ficam')} "
        "fora: a previdência tem o monitor próprio"
    )
