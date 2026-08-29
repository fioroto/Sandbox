"""Coletor do BTG Pactual.

Prateleira grande (~1.500 fundos de 350+ gestoras) renderizada no cliente -
mesma abordagem da XP: Playwright interceptando o trafego JSON da listagem.
"""
from __future__ import annotations

from shelf_monitor.collectors.browser import collect_via_browser
from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer


class BtgCollector(PlatformCollector):
    def _collect_playwright(self) -> list[FundOffer]:
        if not self.platform.shelf_url:
            raise SourceUnavailable("shelf_url nao configurada para o BTG")

        offers, urls = collect_via_browser(
            self.platform_id,
            self.platform.shelf_url,
            user_agent=self.config.user_agent,
            executable_path=self.config.chromium_path,
        )
        self.source_urls.extend(urls)
        return offers
