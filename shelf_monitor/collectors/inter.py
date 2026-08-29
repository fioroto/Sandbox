"""Coletor do Banco Inter.

O Inter publica uma pagina estatica por fundo sob
/pra-voce/investimentos/fundos-de-investimento/<slug>/, o que torna o sitemap.xml
a via mais estavel para enumerar a prateleira - nao depende de renderizacao JS.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable, get
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    parse_int,
    parse_number,
)

_SITEMAP_URLS = [
    "https://inter.co/sitemap.xml",
    "https://inter.co/sitemap-0.xml",
]
_SHELF_PREFIX = "/pra-voce/investimentos/fundos-de-investimento/"
_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_MAX_FUNDS = 400


class InterCollector(PlatformCollector):
    def _collect_web(self) -> list[FundOffer]:
        urls = self._discover_fund_urls()
        if not urls:
            raise SourceUnavailable("nenhuma pagina de fundo encontrada no sitemap")

        offers: list[FundOffer] = []
        for url in urls[:_MAX_FUNDS]:
            offer = self._parse_fund_page(url)
            if offer:
                offers.append(offer)
        return offers

    def _discover_fund_urls(self) -> list[str]:
        found: list[str] = []
        last_error: Exception | None = None

        for sitemap_url in _SITEMAP_URLS:
            try:
                response = get(self.session, sitemap_url, self.config)
            except SourceUnavailable as exc:
                last_error = exc
                continue

            self.source_urls.append(sitemap_url)
            soup = BeautifulSoup(response.text, "xml")

            # sitemap index: segue os sitemaps filhos
            for loc in soup.find_all("loc"):
                url = (loc.text or "").strip()
                if not url:
                    continue
                if url.endswith(".xml") and "sitemap" in url:
                    found.extend(self._urls_from_sitemap(url))
                elif _SHELF_PREFIX in url and url.rstrip("/") != urljoin(
                    url, _SHELF_PREFIX
                ).rstrip("/"):
                    found.append(url)

            if found:
                break

        if not found and last_error:
            raise SourceUnavailable(f"sitemap do Inter inacessivel: {last_error}")

        # Remove o hub e duplicatas preservando a ordem
        hub = f"https://inter.co{_SHELF_PREFIX}"
        unique = dict.fromkeys(u for u in found if u.rstrip("/") != hub.rstrip("/"))
        return list(unique)

    def _urls_from_sitemap(self, url: str) -> list[str]:
        try:
            response = get(self.session, url, self.config)
        except SourceUnavailable:
            return []
        soup = BeautifulSoup(response.text, "xml")
        return [
            loc.text.strip()
            for loc in soup.find_all("loc")
            if loc.text and _SHELF_PREFIX in loc.text
        ]

    def _parse_fund_page(self, url: str) -> FundOffer | None:
        try:
            response = get(self.session, url, self.config)
        except SourceUnavailable:
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        heading = soup.find("h1")
        name = heading.get_text(strip=True) if heading else None
        if not name:
            title = soup.find("title")
            name = title.get_text(strip=True).split("|")[0].strip() if title else None
        if not name:
            return None

        text = soup.get_text(" ", strip=True)
        cnpj_match = _CNPJ_RE.search(text)
        facts = _extract_facts(soup)

        return FundOffer(
            platform=self.platform_id,
            name=name,
            cnpj=normalize_cnpj(cnpj_match.group(0)) if cnpj_match else None,
            manager_name=facts.get("gestor") or facts.get("gestora"),
            admin_name=facts.get("administrador"),
            asset_class=classify_asset_class(
                facts.get("classe"), facts.get("categoria"), name
            ),
            raw_category=facts.get("classe") or facts.get("categoria"),
            min_initial=parse_number(
                facts.get("aplicacao minima") or facts.get("investimento minimo")
            ),
            admin_fee_pct=parse_number(facts.get("taxa de administracao")),
            redemption_quotation_days=parse_int(facts.get("cotizacao do resgate")),
            redemption_payment_days=parse_int(
                facts.get("liquidacao do resgate") or facts.get("pagamento do resgate")
            ),
            detail_url=url,
            extra={"source": "html-scrape"},
        )


def _extract_facts(soup: BeautifulSoup) -> dict[str, str]:
    """Coleta pares rotulo/valor de tabelas e listas de definicao da pagina."""
    from shelf_monitor.normalize import normalize_text

    facts: dict[str, str] = {}

    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            label = normalize_text(cells[0].get_text(" ", strip=True)).lower()
            value = cells[1].get_text(" ", strip=True)
            if label and value:
                facts.setdefault(label, value)

    for dl in soup.find_all("dl"):
        terms = dl.find_all("dt")
        definitions = dl.find_all("dd")
        for term, definition in zip(terms, definitions):
            label = normalize_text(term.get_text(" ", strip=True)).lower()
            value = definition.get_text(" ", strip=True)
            if label and value:
                facts.setdefault(label, value)

    return facts
