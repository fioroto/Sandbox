"""Rotulo e ordem das casas - uma definicao, nao tres.

`_platform_label` existia identico em `aggregate`, `digest` e `spreadsheet`, e
a copia de `aggregate` ainda era importada como nome PRIVADO por seis outros
modulos. O prefixo `_` nao significava nada ali.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from shelf_monitor.config import PLATFORMS

# Rotulos que um modulo troca durante a propria execucao, para o mesmo id. A
# previdencia coleta o Itau pelo catalogo do Private Bank, nao pela prateleira
# Ion: o id da casa continua `itau` (e a cor e a ordem com ele), o canal nao.
# So se escreve aqui por `rotulos_sobrepostos`, que devolve o estado anterior ao
# sair - inclusive com excecao -, entao um teste nao vaza rotulo para o seguinte.
_SOBREPOSTOS: dict[str, str] = {}


@contextmanager
def rotulos_sobrepostos(rotulos: dict[str, str]) -> Iterator[None]:
    """Troca o rotulo de exibicao de algumas casas enquanto o bloco roda."""
    anterior = dict(_SOBREPOSTOS)
    _SOBREPOSTOS.update(rotulos)
    try:
        yield
    finally:
        _SOBREPOSTOS.clear()
        _SOBREPOSTOS.update(anterior)


def rotulo_da_casa(platform_id: str) -> str:
    """Nome de exibicao da casa; o proprio id quando ela nao esta configurada."""
    if platform_id in _SOBREPOSTOS:
        return _SOBREPOSTOS[platform_id]
    cfg = PLATFORMS.get(platform_id)
    return cfg.label if cfg else platform_id


def ordem_canonica(platform_ids: set[str]) -> list[str]:
    """Ordem canonica das casas - a mesma das cores dos graficos.

    A cor segue a plataforma, nunca a posicao no ranking, entao a ordem tem de
    ser estavel entre execucoes e entre superficies.
    """
    ordenados = [pid for pid in PLATFORMS if pid in platform_ids]
    ordenados.extend(sorted(platform_ids - set(PLATFORMS)))
    return ordenados
