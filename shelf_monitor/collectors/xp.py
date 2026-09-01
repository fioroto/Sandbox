"""Coletor da XP - baseline de comparacao do monitor.

A lista de fundos e uma SPA e a pagina devolve 403 a cliente HTTP simples, entao
a unica via publica alem do Open Finance e o navegador real - implementado uma
vez em PlatformCollector._collect_playwright, que esta classe herda sem mudanca.
"""
from __future__ import annotations

from shelf_monitor.collectors.platform import PlatformCollector


class XpCollector(PlatformCollector):
    """Sem especificidade: Open Finance + renderizacao herdada."""
