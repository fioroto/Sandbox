"""Coletor do Itau (Ion).

O catalogo Personnalite e a visao publica mais completa da prateleira do Itau.
A pagina e majoritariamente server-rendered, entao o scraping direto costuma
bastar; se vier vazia, o PlatformCollector ja tera tentado o Open Finance antes.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from shelf_monitor.collectors.browser import extract_fund_dicts, to_offer
from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable, get
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import classify_asset_class, normalize_cnpj, parse_money

_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_EMBEDDED_JSON_RE = re.compile(
    r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', re.DOTALL
)


class ItauCollector(PlatformCollector):
    def _collect_web(self) -> list[FundOffer]:
        if not self.platform.shelf_url:
            raise SourceUnavailable("shelf_url nao configurada para o Itau")

        response = get(self.session, self.platform.shelf_url, self.config)
        self.source_urls.append(self.platform.shelf_url)

        offers = self._from_embedded_json(response.text)
        if offers:
            return offers
        return self._from_dom(response.text)

    def _from_embedded_json(self, html: str) -> list[FundOffer]:
        """O catalogo hidrata a tabela a partir de um JSON embutido na pagina."""
        import json

        offers: list[FundOffer] = []
        seen: set[str] = set()
        for match in _EMBEDDED_JSON_RE.finditer(html):
            try:
                payload = json.loads(match.group(1))
            except ValueError:
                continue
            for item in extract_fund_dicts(payload):
                offer = to_offer(self.platform_id, item)
                if offer and offer.identity not in seen:
                    seen.add(offer.identity)
                    offers.append(offer)
        return offers

    def _from_dom(self, html: str) -> list[FundOffer]:
        """Fallback: le a tabela renderizada do catalogo."""
        soup = BeautifulSoup(html, "html.parser")
        offers: list[FundOffer] = []
        seen: set[str] = set()

        for row in soup.find_all("tr"):
            cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
            if len(cells) < 2 or not cells[0]:
                continue

            name = cells[0]
            row_text = " ".join(cells)
            cnpj_match = _CNPJ_RE.search(row_text)
            link = row.find("a", href=True)
            # O catalogo usa caminhos relativos; guardar o href cru deixaria o
            # link quebrado no dashboard.
            detail_url = (
                urljoin(self.platform.shelf_url, link["href"]) if link else None
            )

            offer = FundOffer(
                platform=self.platform_id,
                name=name,
                cnpj=normalize_cnpj(cnpj_match.group(0)) if cnpj_match else None,
                asset_class=classify_asset_class(cells[1] if len(cells) > 1 else None, name),
                raw_category=cells[1] if len(cells) > 1 else None,
                min_initial=_first_currency(cells[2:]),
                detail_url=detail_url,
            )
            if offer.identity not in seen:
                seen.add(offer.identity)
                offers.append(offer)

        if not offers:
            raise SourceUnavailable(
                "catalogo do Itau nao retornou linhas parseaveis "
                "(possivel renderizacao client-side ou mudanca de layout)"
            )
        return offers


def _first_currency(cells: list[str]) -> float | None:
    for cell in cells:
        if "R$" in cell:
            value = parse_money(cell)
            if value is not None:
                return value
    return None
