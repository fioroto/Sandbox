"""Coletor do BTG Pactual.

A evidencia do `diagnose --browser` (2026-08-31, maquina do usuario) mostrou que
a pagina /fundos-de-investimento/produtos e alimentada por uma API publica:

    GET /services/api/funds-public/public/funds/list/v2?page=N&size=K&orderBy=NOME

O envelope e {items, total, page, total_pages}; na medicao havia 693 fundos na
prateleira. Isso e diferente - e complementar - dos ~9.950 do Open Finance: la
esta tudo que a casa administra; aqui, o que ela de fato distribui no portal.

A pagina pede size=7 por vez (99 paginas); a API aceita paginas maiores. O
coletor tenta a API direto por HTTP (API_JSON); se o WAF barrar cliente simples,
o caminho Playwright pagina a mesma API por dentro do navegador, com os cookies
e o TLS dele.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable, get_json
from shelf_monitor.models import FundOffer, PerformanceFee
from shelf_monitor.normalize import classify_asset_class, normalize_cnpj, parse_number

_API_PATH = "/services/api/funds-public/public/funds/list/v2"
_BASE_URL = "https://investimentos.btgpactual.com"
_PAGE_SIZE = 100
_MAX_PAGES = 400  # trava: 693 fundos a 7/pagina sao ~100 paginas
_DPLUS_RE = re.compile(r"D\s*\+\s*(\d+)", re.IGNORECASE)


def parse_dplus(raw: object) -> int | None:
    """"D+30" -> 30. E como o BTG publica cotizacao e liquidacao."""
    if raw is None:
        return None
    match = _DPLUS_RE.search(str(raw))
    return int(match.group(1)) if match else None


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    return None


def offer_from_btg_item(item: dict) -> FundOffer | None:
    """Mapeia um item do funds/list/v2 usando o schema observado na evidencia.

    Os campos ricos moram em Detail e Detail.detail - o extrator generico so
    alcanca Name e CNPJ do nivel de cima, e foi por isso que a primeira captura
    saiu com 7 fundos pelados.
    """
    if not isinstance(item, dict):
        return None
    name = _as_text(item.get("Name")) or _as_text(item.get("NameOriginal"))
    if not name:
        return None

    detail = item.get("Detail") if isinstance(item.get("Detail"), dict) else {}
    inner = detail.get("detail") if isinstance(detail.get("detail"), dict) else {}

    category = _as_text(detail.get("categoryBTG"))
    subcategory = _as_text(detail.get("subcategoryBTG"))

    # administrationFeeType diz a unidade; so PERCENTAGE vira taxa percentual.
    admin_fee = None
    if inner.get("administrationFeeType") == "PERCENTAGE":
        admin_fee = parse_number(inner.get("administrationFee"))

    perf_raw = parse_number(inner.get("performanceFee"))
    performance_fee = None
    if perf_raw:
        performance_fee = PerformanceFee(
            amount_pct=perf_raw,
            benchmark=_as_text(detail.get("benchmarkName")),
        )

    apply_open = detail.get("applyOpenPortal")
    closed = (not apply_open) if isinstance(apply_open, bool) else None

    restricted = bool(
        detail.get("restrictedOrExclusive")
        or detail.get("isExclusiveFund")
        or detail.get("isExclusiveFundPortal")
    )

    extra = {
        key: value
        for key, value in (
            ("restrito_casa", restricted or None),
            ("subcategoria_btg", subcategory),
            ("risco", _as_text(detail.get("riskName"))),
            ("classificacao_anbima", _as_text(inner.get("anbimaRating"))),
            ("pl", parse_number(detail.get("netEquity"))),
            ("publico", _as_text(detail.get("investmentType"))),
            ("destaque_assessor", detail.get("featuredProductAssessor")),
        )
        if value is not None
    }

    return FundOffer(
        platform="btg",
        name=name,
        cnpj=normalize_cnpj(_as_text(item.get("CNPJ")) or _as_text(detail.get("cnpj"))),
        isin=_as_text(detail.get("isinCode")),
        manager_name=_as_text(inner.get("manager")),
        admin_name=_as_text(inner.get("administrator")),
        asset_class=classify_asset_class(category, subcategory, name),
        raw_category=category,
        min_initial=parse_number(
            detail.get("minimumInitialInvestment")
            if detail.get("minimumInitialInvestment") is not None
            else inner.get("minimumInitialInvestment")
        ),
        admin_fee_pct=admin_fee,
        performance_fee=performance_fee,
        redemption_quotation_days=parse_dplus(inner.get("rescueQuota")),
        redemption_payment_days=parse_dplus(inner.get("rescuefinancialSettlement")),
        taxation=_as_text(detail.get("nmTaxationPursued")),
        closed_for_investment=closed,
        extra=extra,
    )


def paginate_btg_api(
    fetch_json: Callable[[str], Any],
    base_url: str,
    warnings: list[str] | None = None,
) -> tuple[list[FundOffer], list[str]]:
    """Percorre o funds/list/v2 inteiro com a funcao de fetch que for dada.

    A mesma logica serve ao caminho HTTP direto e ao caminho por dentro do
    navegador - so muda quem executa o GET. Quem passar `warnings` recebe o
    aviso de teto atingido, que rebaixa o snapshot para parcial.
    """
    offers: list[FundOffer] = []
    seen: set[str] = set()
    urls: list[str] = []
    page = 1
    total_pages = 1

    while page <= min(total_pages, _MAX_PAGES):
        url = (
            f"{base_url}{_API_PATH}?page={page}&size={_PAGE_SIZE}"
            f"&orderBy=NOME&sortingDirection=ASC"
        )
        payload = fetch_json(url)
        if not isinstance(payload, dict) or "items" not in payload:
            raise SourceUnavailable(
                f"funds/list/v2 respondeu fora do formato esperado na página {page}"
            )
        if page == 1:
            urls.append(url)
            total_pages_raw = payload.get("total_pages")
            items_in_page = len(payload.get("items") or [])
            # O server pode ignorar o size pedido (a pagina do site usa 7);
            # total_pages ja vem calculado para o size efetivo.
            total_pages = int(total_pages_raw) if total_pages_raw else 1
            if items_in_page == 0:
                break

        items = payload.get("items") or []
        if not items:
            break
        for item in items:
            offer = offer_from_btg_item(item)
            if offer and offer.identity not in seen:
                seen.add(offer.identity)
                offers.append(offer)
        page += 1

    if warnings is not None and total_pages > _MAX_PAGES:
        # Mesmo tratamento do Open Finance: prateleira cortada nao pode sair
        # como coleta inteira.
        warnings.append(
            f"paginação do funds/list/v2 truncada em {_MAX_PAGES} de "
            f"{total_pages} páginas ({len(offers)} fundos coletados) - "
            "prateleira incompleta"
        )

    return offers, urls


class BtgCollector(PlatformCollector):
    @property
    def _base_url(self) -> str:
        """Origem da API = origem da shelf_url; se o BTG trocar de dominio, a
        config acompanha e a API vai junto."""
        from urllib.parse import urlsplit

        if self.platform.shelf_url:
            parts = urlsplit(self.platform.shelf_url)
            if parts.scheme and parts.netloc:
                return f"{parts.scheme}://{parts.netloc}"
        return _BASE_URL

    def _collect_web(self) -> list[FundOffer]:
        """API publica via HTTP direto - o caminho barato quando o WAF deixa."""
        def fetch(url: str):
            return get_json(self.session, url, self.config)

        offers, urls = paginate_btg_api(fetch, self._base_url, self.warnings)
        self.source_urls.extend(urls)
        return offers

    def _collect_playwright(self) -> list[FundOffer]:
        """Renderiza a pagina e pagina a API por dentro do navegador.

        Vale quando o HTTP direto e barrado: o page.request usa os cookies e o
        TLS do proprio Chromium, que a evidencia mostrou passar (HTTP 200).
        """
        from shelf_monitor.collectors.browser import collect_via_browser

        if not self.platform.shelf_url:
            raise SourceUnavailable("shelf_url não configurada para o BTG")

        def paginate_in_page(page) -> list[tuple[str, Any]]:
            collected: list[tuple[str, Any]] = []

            def fetch(url: str):
                response = page.request.get(url)
                if response.status >= 400:
                    raise SourceUnavailable(f"HTTP {response.status} em {url}")
                payload = response.json()
                collected.append((url, payload))
                return payload

            # O retorno vai para capture.payloads; o mapper abaixo le de la.
            try:
                paginate_btg_api(fetch, self._base_url)
            except Exception:
                pass
            return collected

        def mapper(url: str, payload: Any) -> list[FundOffer] | None:
            if _API_PATH not in url:
                return None  # deixa o caminho generico tratar
            items = payload.get("items") if isinstance(payload, dict) else None
            if not isinstance(items, list):
                return None
            offers = [offer_from_btg_item(item) for item in items]
            return [o for o in offers if o]

        offers, urls = collect_via_browser(
            self.platform_id,
            self.platform.shelf_url,
            user_agent=self.config.user_agent,
            executable_path=self.config.chromium_path,
            debug_dir=self.config.debug_dir if self.config.browser_debug else None,
            headed=self.config.browser_headed,
            channel=self.config.browser_channel,
            on_page=paginate_in_page,
            payload_mapper=mapper,
            warnings=self.warnings,
        )
        self.source_urls.extend(urls)
        return offers
