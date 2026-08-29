"""Coleta de prateleiras que sao SPA, via Playwright headless.

XP e BTG renderizam a listagem no cliente, entao o HTML servido nao contem os
fundos. A estrategia e abrir a pagina com Chromium e interceptar as respostas
XHR/fetch: o JSON que alimenta a listagem tem mais campos (e e mais estavel de
parsear) do que o DOM renderizado.

Chromium ja vem instalado no ambiente (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers).
"""
from __future__ import annotations

import json
from typing import Any

from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    normalize_text,
    parse_int,
    parse_money,
    parse_number,
)

_NAV_TIMEOUT_MS = 60_000
_SETTLE_MS = 6_000
_SCROLL_ROUNDS = 12

# Chaves que costumam carregar os campos de interesse, em varias grafias.
_NAME_KEYS = ("name", "nome", "fundname", "nomefundo", "displayname", "razaosocial")
_CNPJ_KEYS = ("cnpj", "cnpjnumber", "cnpjfundo", "documentnumber")
_CATEGORY_KEYS = ("category", "categoria", "classe", "class", "anbimacategory", "tipo")
_MANAGER_KEYS = ("manager", "gestor", "gestora", "fundmanager", "assetmanager")
_ADMIN_KEYS = ("admin", "administrador", "administrator")
_MIN_KEYS = (
    "minimuminitialapplication", "minimumamount", "aplicacaominima",
    "valorminimo", "minimoinicial", "initialapplication", "minimumapplication",
)
_ADMIN_FEE_KEYS = ("adminfee", "taxaadministracao", "administrationfee", "maxadminfee")
_PERF_FEE_KEYS = ("performancefee", "taxaperformance")
_QUOTATION_KEYS = ("quotationdays", "diascotizacao", "cotizacaoresgate", "conversiondays")
_PAYMENT_KEYS = ("paymentdays", "diasliquidacao", "liquidacaoresgate", "settlementdays")
_CLOSED_KEYS = ("closed", "fechado", "isclosed", "closedforapplication", "encerrado")


def _norm_key(key: str) -> str:
    return normalize_text(key).replace(" ", "").lower()


def _pick(item: dict, keys: tuple[str, ...]) -> Any:
    """Busca a primeira chave que casar, ignorando caixa/acento/underscore."""
    normalized = {_norm_key(k): v for k, v in item.items()}
    for key in keys:
        value = normalized.get(_norm_key(key))
        if value not in (None, "", []):
            return value
    return None


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        for key in ("name", "nome", "description", "label", "value"):
            if isinstance(value.get(key), str):
                return value[key].strip() or None
        return None
    return str(value)


def looks_like_fund(item: object) -> bool:
    """Um dict e um fundo se tem nome e ao menos um campo caracteristico."""
    if not isinstance(item, dict):
        return False
    if not _as_text(_pick(item, _NAME_KEYS)):
        return False
    markers = (_CNPJ_KEYS, _CATEGORY_KEYS, _MIN_KEYS, _ADMIN_FEE_KEYS, _MANAGER_KEYS)
    return any(_pick(item, keys) is not None for keys in markers)


def extract_fund_dicts(payload: object, depth: int = 0) -> list[dict]:
    """Varre um JSON arbitrario e junta os objetos que parecem fundos."""
    if depth > 8:
        return []

    found: list[dict] = []
    if isinstance(payload, list):
        matches = [item for item in payload if looks_like_fund(item)]
        if matches:
            found.extend(matches)
        else:
            for item in payload:
                found.extend(extract_fund_dicts(item, depth + 1))
    elif isinstance(payload, dict):
        if looks_like_fund(payload) and not any(
            isinstance(v, (list, dict)) and extract_fund_dicts(v, depth + 1)
            for v in payload.values()
        ):
            found.append(payload)
        else:
            for value in payload.values():
                found.extend(extract_fund_dicts(value, depth + 1))
    return found


def _parse_min_initial(raw: Any) -> float | None:
    """Escolhe o parser pelo formato: texto em reais tem ponto de milhar."""
    if isinstance(raw, str) and ("R$" in raw or "," in raw):
        return parse_money(raw)
    return parse_number(raw)


def to_offer(platform_id: str, item: dict) -> FundOffer | None:
    """Mapeia um dict de fundo vindo de JSON arbitrario para FundOffer.

    A origem nao entra aqui: quem sabe de que fonte veio e o merge, que a
    registra em `sources` ao unir os resultados."""
    name = _as_text(_pick(item, _NAME_KEYS))
    if not name:
        return None

    category = _as_text(_pick(item, _CATEGORY_KEYS))
    closed_raw = _pick(item, _CLOSED_KEYS)
    closed = bool(closed_raw) if isinstance(closed_raw, bool) else None

    return FundOffer(
        platform=platform_id,
        name=name,
        cnpj=normalize_cnpj(_as_text(_pick(item, _CNPJ_KEYS))),
        manager_name=_as_text(_pick(item, _MANAGER_KEYS)),
        admin_name=_as_text(_pick(item, _ADMIN_KEYS)),
        asset_class=classify_asset_class(category, name),
        raw_category=category,
        # APIs internas as vezes devolvem o minimo ja formatado ("R$ 25.000"):
        # numero nativo e parse_number; texto em reais e parse_money.
        min_initial=_parse_min_initial(_pick(item, _MIN_KEYS)),
        admin_fee_pct=parse_number(_pick(item, _ADMIN_FEE_KEYS)),
        redemption_quotation_days=parse_int(_pick(item, _QUOTATION_KEYS)),
        redemption_payment_days=parse_int(_pick(item, _PAYMENT_KEYS)),
        closed_for_investment=closed,
    )


def collect_via_browser(
    platform_id: str,
    url: str,
    *,
    user_agent: str,
    storage_state: str | None = None,
    executable_path: str | None = None,
) -> tuple[list[FundOffer], list[str]]:
    """Abre a pagina, intercepta respostas JSON e extrai os fundos encontrados.

    Devolve (fundos, urls_de_origem). Lanca SourceUnavailable se o Playwright
    nao estiver instalado ou a navegacao falhar.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SourceUnavailable(
            "playwright nao instalado: pip install playwright"
        ) from exc

    captured: list[dict] = []
    seen_urls: list[str] = []

    def on_response(response) -> None:
        content_type = (response.headers or {}).get("content-type", "")
        if "json" not in content_type.lower():
            return
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - resposta nao parseavel e so ruido
            return
        items = extract_fund_dicts(payload)
        if items:
            captured.extend(items)
            seen_urls.append(response.url)

    try:
        with sync_playwright() as playwright:
            launch_args: dict[str, Any] = {"headless": True}
            if executable_path:
                launch_args["executable_path"] = executable_path
            try:
                browser = playwright.chromium.launch(**launch_args)
            except Exception as exc:  # noqa: BLE001
                if "Executable doesn't exist" in str(exc):
                    raise SourceUnavailable(
                        "Chromium do Playwright ausente. Rode `playwright install "
                        "chromium`, ou aponte um binario ja instalado em "
                        "SHELF_CHROMIUM_PATH."
                    ) from exc
                raise
            context_args: dict[str, Any] = {
                "user_agent": user_agent,
                "locale": "pt-BR",
                "viewport": {"width": 1440, "height": 900},
            }
            if storage_state:
                context_args["storage_state"] = storage_state
            context = browser.new_context(**context_args)
            page = context.new_page()
            page.on("response", on_response)

            page.goto(url, timeout=_NAV_TIMEOUT_MS, wait_until="domcontentloaded")
            page.wait_for_timeout(_SETTLE_MS)

            # Listagens carregam por scroll infinito ou paginacao lazy.
            for _ in range(_SCROLL_ROUNDS):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1200)

            html = page.content()
            context.close()
            browser.close()
    except SourceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        # As excecoes do Playwright trazem varias linhas de contexto do browser.
        detail = " ".join(str(exc).split())[:160]
        raise SourceUnavailable(f"navegacao falhou em {url}: {detail}") from exc

    if not captured:
        captured = _from_embedded_state(html)
        if captured:
            seen_urls.append(f"{url} (estado embutido)")

    offers: list[FundOffer] = []
    seen_identities: set[str] = set()
    for item in captured:
        offer = to_offer(platform_id, item)
        if offer and offer.identity not in seen_identities:
            seen_identities.add(offer.identity)
            offers.append(offer)

    if not offers:
        raise SourceUnavailable(
            f"nenhum fundo identificado no trafego JSON de {url} "
            "(possivel bloqueio anti-bot ou mudanca de layout)"
        )

    return offers, seen_urls or [url]


def _from_embedded_state(html: str) -> list[dict]:
    """Fallback: alguns sites embutem o estado inicial num <script> JSON."""
    import re

    found: list[dict] = []
    for match in re.finditer(
        r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL
    ):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        found.extend(extract_fund_dicts(payload))

    for pattern in (
        r"__NEXT_DATA__\s*=\s*({.*?})\s*</script>",
        r"__NUXT__\s*=\s*({.*?});",
        r"window\.__INITIAL_STATE__\s*=\s*({.*?});",
    ):
        for match in re.finditer(pattern, html, re.DOTALL):
            try:
                payload = json.loads(match.group(1))
            except ValueError:
                continue
            found.extend(extract_fund_dicts(payload))

    return found
