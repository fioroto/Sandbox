"""Rotulo e ordem das casas - uma definicao, nao tres.

`_platform_label` existia identico em `aggregate`, `digest` e `spreadsheet`, e
a copia de `aggregate` ainda era importada como nome PRIVADO por seis outros
modulos. O prefixo `_` nao significava nada ali.
"""
from __future__ import annotations

from shelf_monitor.config import PLATFORMS


def rotulo_da_casa(platform_id: str) -> str:
    """Nome de exibicao da casa; o proprio id quando ela nao esta configurada."""
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
