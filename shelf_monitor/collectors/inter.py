"""Coletor do Banco Inter.

O Inter publica uma pagina estatica por fundo sob
/pra-voce/investimentos/fundos-de-investimento/<slug>/, o que torna o sitemap.xml
a via mais estavel para enumerar a prateleira - nao depende de renderizacao JS.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable, get
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    parse_int,
    parse_money,
    parse_number,
)

# So o sitemap.xml existe: o diagnostico na maquina do usuario mostrou 574 URLs
# de fundos nele, e /sitemap-0.xml devolvendo 404. Manter o segundo so poluia o
# log com uma falha esperada a cada coleta.
_SITEMAP_URLS = ["https://inter.co/sitemap.xml"]
_SHELF_PREFIX = "/pra-voce/investimentos/fundos-de-investimento/"
_HUB_URL = f"https://inter.co{_SHELF_PREFIX}"
_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
# O sitemap trazia 574 fundos em 2026-08-31; o teto antigo (400) cortava 174
# deles em silencio. O teto existe so contra um sitemap desgovernado - e quando
# for atingido a coleta avisa e o snapshot sai PARTIAL.
_MAX_FUNDS = 1_000
# Uma pagina que falha em 574 e ruido de rede. A partir desta fracao (com um
# piso absoluto, para nao rebaixar uma coleta pequena por duas falhas) a
# prateleira coletada deixa de ser confiavel como baseline.
_MAX_LOST_PAGE_RATIO = 0.05
_MIN_LOST_PAGES = 5

# Termos que indicam que o fundo foi encerrado ou esta em liquidacao.
# O sitemap continua publicando a URL, mas o fundo nao faz parte da
# prateleira ativa - entra como closed_for_investment=True.
_CLOSED_FUND_RE = re.compile(
    r"(?:fundo\s+)?(?:encerrado|em\s+liquidac[aã]o|resgate\s+total"
    r"|encerramento|descontinuado|fundo\s+fechado\s+para\s+aplicac[aã]o)",
    re.IGNORECASE,
)


def _detect_closed_fund(text: str) -> bool:
    """Detecta se a pagina indica um fundo encerrado ou em liquidacao."""
    return _CLOSED_FUND_RE.search(text) is not None


class InterCollector(PlatformCollector):
    _fetch_failures = 0
    _page_data_failures = 0

    def _collect_web(self) -> list[FundOffer]:
        urls = self._discover_fund_urls()
        if not urls:
            raise SourceUnavailable("nenhuma página de fundo encontrada no sitemap")

        if len(urls) > _MAX_FUNDS:
            self.warnings.append(
                f"sitemap com {len(urls)} fundos, coletados só {_MAX_FUNDS} "
                "(teto de segurança) - prateleira incompleta"
            )

        # Zerados aqui, e nao no __init__, para que uma segunda estrategia da
        # mesma casa nao herde a contagem da primeira.
        self._fetch_failures = 0
        self._page_data_failures = 0

        offers: list[FundOffer] = []
        for url in urls[:_MAX_FUNDS]:
            offer = self._parse_fund_page(url)
            if offer:
                offers.append(offer)

        total = min(len(urls), _MAX_FUNDS)
        if (
            self._fetch_failures >= _MIN_LOST_PAGES
            and self._fetch_failures / total >= _MAX_LOST_PAGE_RATIO
        ):
            # Pagina que nao abriu e fundo perdido: sem isto a prateleira encolhe
            # em silencio e o diff do dia seguinte le a perda como saida.
            self.warnings.append(
                f"{self._fetch_failures} de {total} páginas de fundo falharam na "
                "coleta - prateleira incompleta"
            )
        if total and self._page_data_failures >= total // 2:
            # Aqui a prateleira esta inteira: o que faltou foram os fatos. Vira
            # observacao, nao rebaixamento - senao uma mudanca no Gatsby
            # bloquearia baseline confiavel por tempo indeterminado.
            note = (
                f"page-data.json falhou em {self._page_data_failures} de {total} "
                "páginas - fatos (taxa, mínimo, gestor) incompletos"
            )
            self.partial_reason = (
                f"{self.partial_reason} | {note}" if self.partial_reason else note
            )
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
                elif _SHELF_PREFIX in url:
                    found.append(url)

            if found:
                break

        if not found and last_error:
            raise SourceUnavailable(f"sitemap do Inter inacessível: {last_error}")

        # O hub e a pagina de listagem, nao um fundo: fica de fora.
        hub = _HUB_URL.rstrip("/")
        unique = dict.fromkeys(u for u in found if u.rstrip("/") != hub)
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
            self._fetch_failures += 1
            return None

        offer = self._offer_from_html(url, response.text)
        if offer is None:
            return None

        # O site e Gatsby: cada pagina tem um JSON estatico em
        # /page-data/<caminho>/page-data.json (evidencia de 2026-08-31). A
        # coleta por HTML trazia nome e CNPJ mas nenhum fato (taxa, minimo,
        # gestor nao ficam em tabela/dl); o JSON e a chance de recupera-los.
        enriched = self._offer_from_page_data(url)
        if enriched is not None:
            offer = _fill_missing(offer, enriched)
        return offer

    def _offer_from_page_data(self, url: str) -> FundOffer | None:
        from urllib.parse import urlsplit

        from shelf_monitor.collectors.browser import extract_fund_dicts, to_offer

        path = urlsplit(url).path.rstrip("/")
        if not path:
            return None
        page_data_url = f"https://inter.co/page-data{path}/page-data.json"
        try:
            response = get(self.session, page_data_url, self.config)
            payload = response.json()
        except (SourceUnavailable, ValueError):
            self._page_data_failures += 1
            return None

        for item in extract_fund_dicts(payload):
            offer = to_offer(self.platform_id, item, url)
            if offer:
                return offer
        return None

    def _offer_from_html(self, url: str, html: str) -> FundOffer | None:
        soup = BeautifulSoup(html, "html.parser")
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
        closed = _detect_closed_fund(text)

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
            min_initial=parse_money(
                facts.get("aplicacao minima") or facts.get("investimento minimo")
            ),
            admin_fee_pct=parse_number(facts.get("taxa de administracao")),
            redemption_quotation_days=parse_int(facts.get("cotizacao do resgate")),
            redemption_payment_days=parse_int(
                facts.get("liquidacao do resgate") or facts.get("pagamento do resgate")
            ),
            closed_for_investment=closed if closed else None,
            detail_url=url,
        )


def _fill_missing(base: FundOffer, other: FundOffer) -> FundOffer:
    """Completa os campos vazios de `base` com o que `other` tiver.

    O HTML manda no nome e no CNPJ (comprovados na coleta real); o page-data
    so preenche o que faltou - se o formato dele mudar, nada piora.
    """
    from dataclasses import fields, replace

    updates = {}
    for f in fields(FundOffer):
        if f.name in ("platform", "name", "cnpj", "sources", "extra"):
            continue
        if getattr(base, f.name) is None and getattr(other, f.name) is not None:
            updates[f.name] = getattr(other, f.name)
    # asset_class nao e None-avel: OUTROS conta como "vazio".
    from shelf_monitor.models import AssetClass

    if base.asset_class == AssetClass.OUTROS and other.asset_class != AssetClass.OUTROS:
        updates["asset_class"] = other.asset_class
    return replace(base, **updates) if updates else base


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
