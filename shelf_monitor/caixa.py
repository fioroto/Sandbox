"""Fundos caixa/simples: o produto padrao de caixa, fora das analises.

Pedido de 16/10/2026: o fundo caixa e o simples sao produto padrao - toda casa
tem o seu - e nao agregam a analise. Enchiam o topo das captacoes (BTGP Tesouro
Selic, Itau Privilege Referenciado DI) e apareciam como gap.

A regra e a versao "nucleo", escolhida pelo usuario: renda fixa com o rotulo
ANBIMA de caixa - Renda Fixa Simples, Duracao Baixa Soberano, Duracao Baixa
Grau de Investimento - ou com a marca no nome: Simples, Selic, Referenciado DI.
O rotulo sozinho nao basta: o BTGP Tesouro Selic esta como "Duracao Livre Grau
de Investimento" no cadastro. Ficam DENTRO, de proposito, o fundo de CDB e o de
credito bancario (BTG CDB Plus, BTG Credito Bancario): a versao mais ampla, que
os tiraria tambem, nao foi a escolhida.

Medido no relatorio de 14/10/2026: 81 dos 1.334 fundos com informe nas vitrines
(7,9 de 23,1 bi da captacao positiva em 3 meses), 14 dos 453 gaps reais e 1 dos
54 fundos da grade.

Sai da captacao, dos gaps, da rentabilidade e do custo, e cada bloco diz quantos
tirou (`caixa_fora`). As entradas e saidas de vitrine, o radar da CVM e os
blocos descritivos da vitrine continuam com todos: sao movimento e inventario,
nao analise.
"""
from __future__ import annotations

import re
from collections.abc import Callable

from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM
from shelf_monitor.models import AssetClass, FundOffer
from shelf_monitor.normalize import normalize_text
from shelf_monitor.texto import contagem

# Rotulos ANBIMA de caixa, ja normalizados. O cadastro trunca o rotulo em 40
# caracteres ("Renda Fixa Duracao Baixa Grau de Invest."), entao vale o prefixo.
_ROTULOS_DE_CAIXA = (
    "RENDA FIXA SIMPLES",
    "RENDA FIXA DURACAO BAIXA SOBERANO",
    "RENDA FIXA DURACAO BAIXA GRAU DE INVEST",
)
# A marca de caixa no nome, ja normalizado ("Ref. DI" vira "REF DI").
_NOME_DE_CAIXA = re.compile(r"\bSIMPLES\b|\bSELIC\b|\bREFERENCIADO DI\b|\bREF DI\b|\bRF DI\b|\bDI CP\b")


def e_caixa(nome: str | None, categoria: str | None) -> bool:
    """Fundo caixa/simples: renda fixa com rotulo de caixa ou a marca no nome."""
    rotulo = normalize_text(categoria)
    if not rotulo.startswith("RENDA FIXA"):
        return False
    return rotulo.startswith(_ROTULOS_DE_CAIXA) or bool(_NOME_DE_CAIXA.search(normalize_text(nome)))


def oferta_caixa(fund: FundOffer) -> bool:
    """`e_caixa` de um fundo de vitrine, com o rotulo das mesmas fontes de
    `aggregate.anbima_category`: o cadastro, o rotulo publicado e a classe."""
    categoria = fund.extra.get(KEY_CATEGORIA) or fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM)
    if not categoria and fund.asset_class == AssetClass.RENDA_FIXA:
        categoria = "Renda Fixa"
    return e_caixa(fund.name, categoria)


def sem_os(
    vitrines: dict[str, list[FundOffer]], regra: Callable[[FundOffer], bool]
) -> tuple[dict[str, list[FundOffer]], int]:
    """As vitrines sem os fundos que a `regra` tira, e quantos fundos distintos sairam.

    O mesmo corte serve ao caixa e a previdencia (`shelf_monitor.previdencia_fora`).
    """
    fora: set[str] = set()
    limpas: dict[str, list[FundOffer]] = {}
    for platform_id, fundos in vitrines.items():
        mantidos = []
        for fundo in fundos:
            if regra(fundo):
                fora.add(fundo.cnpj or f"{platform_id}:{fundo.name}")
            else:
                mantidos.append(fundo)
        limpas[platform_id] = mantidos
    return limpas, len(fora)


def sem_caixa(
    vitrines: dict[str, list[FundOffer]]
) -> tuple[dict[str, list[FundOffer]], int]:
    """As vitrines sem os fundos caixa/simples, e quantos fundos distintos sairam."""
    return sem_os(vitrines, oferta_caixa)


def nota_caixa(fora: int | None) -> str | None:
    """A frase que toda superficie usa para dizer quantos fundos caixa sairam."""
    if not fora:
        return None
    return (
        f"{contagem(fora, 'fundo caixa/simples fica', 'fundos caixa/simples ficam')} "
        "fora (Renda Fixa Simples, Selic e DI): produto padrão, que toda casa tem"
    )
