"""Coletor do Itau (Ion).

A lista publica e a de rentabilidade (`/investimentos/fundos/rentabilidade`).
A area Personnalite, usada antes, devolve 403 fora do contexto de cliente - o
diagnostico na maquina do usuario confirmou isso.

A pagina e uma aplicacao e barra cliente HTTP simples com 403; so navegador real
passa. A leitura da tabela renderizada, que antes vivia aqui, virou parte de
`browser.extract_from_dom` - as tres casas que precisam de navegador leem o DOM
pelo mesmo caminho.
"""
from __future__ import annotations

from shelf_monitor.collectors.platform import PlatformCollector


class ItauCollector(PlatformCollector):
    """Sem especificidade: Open Finance + renderizacao herdada."""
