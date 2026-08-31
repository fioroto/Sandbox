"""Coletor do BTG Pactual.

A prateleira publica esta em /fundos-de-investimento/produtos (a raiz e pagina
institucional e nao lista nada) e e renderizada no cliente. O Open Finance da
casa publica ~9.950 fundos - e a fonte principal; a pagina acrescenta a visao
comercial (o que o BTG destaca), nao a lista bruta.
"""
from __future__ import annotations

from shelf_monitor.collectors.platform import PlatformCollector


class BtgCollector(PlatformCollector):
    """Sem especificidade: Open Finance + renderizacao herdada."""
