"""Fixtures compartilhadas pela suite.

Modulo normal e nao `conftest.py` de proposito: a suite roda com
`python -m unittest discover -s tests`, e `conftest` so e carregado
automaticamente pelo pytest. Como um modulo, funciona nos dois.

Aqui mora o que era identico entre arquivos ou o que NAO PODE divergir entre
eles. Os construtores de `offer` e `snapshot` continuam locais: cada arquivo
de teste precisa de defaults diferentes, e unifica-los enfraqueceria os
asserts em vez de simplificar.
"""
from __future__ import annotations

import tempfile

from shelf_monitor.config import Config

# Faixa de CNPJ por casa, em milhares. Sem isso duas vitrines montadas pelos
# helpers teriam os MESMOS CNPJs e nada seria exclusivo de ninguem - o teste de
# exclusivos passaria a medir a colisao do fixture, e nao a funcao. Esta regra
# estava escrita em test_competitive.py e redescoberta, com outros valores, em
# test_retail_flow.py; e conhecimento que nao pode divergir entre arquivos.
FAIXA_CNPJ = {"xp": 0, "btg": 1, "itau": 2, "inter": 3, "nu": 4}
FAIXA_PADRAO = 9


def base_cnpj(platform: str) -> int:
    """Primeiro CNPJ da faixa reservada a esta casa."""
    return FAIXA_CNPJ.get(platform, FAIXA_PADRAO) * 1_000


def temp_config() -> tuple[Config, tempfile.TemporaryDirectory]:
    """Config apontando para um diretorio temporario, com as pastas criadas.

    Devolve o TemporaryDirectory junto porque quem chama tem de guarda-lo: se
    ele for coletado, o diretorio some no meio do teste.
    """
    tmp = tempfile.TemporaryDirectory()
    config = Config(data_dir=tmp.name)
    config.ensure_dirs()
    return config, tmp
