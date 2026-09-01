"""Coletor do Itau.

A lista publica e a de rentabilidade (`/investimentos/fundos/rentabilidade`).
A evidencia de 2026-08-31 (navegador com janela, na maquina do usuario) mostrou:

- cliente HTTP simples e Chromium headless levam 403 da Akamai no documento;
  com o Chrome real do usuario (SHELF_BROWSER_HEADED=1) a pagina abre;
- a tabela e paginada em ~10 fundos por pagina (45 paginas na medicao, ~450
  fundos), com botao `data-testid="page__arrow-next"`;
- as colunas: nome, risco, aplicacao inicial, retornos (12m/ano/mes), taxa
  maxima do produto, resgate - o cabecalho nomeia cada uma;
- ha um XHR de ~1,5 MB (gateway `apicd.cloud.itau.com.br/charon/...`) que deve
  carregar o dataset inteiro; o corpo dele fica em capture quando parsear -
  o payload_mapper aqui nao depende dele.

O coletor clica "proxima" ate o fim, le a tabela renderizada de cada pagina e
mapeia pelas colunas do cabecalho - nunca por posicao fixa.
"""
from __future__ import annotations

import re
from typing import Any

from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import classify_asset_class, normalize_text, parse_money

_NEXT_SELECTOR = '[data-testid="page__arrow-next"]'
# 45 paginas na medicao; folga para a prateleira crescer sem virar loop infinito.
_MAX_TABLE_PAGES = 150
_PAGE_CHANGE_TIMEOUT_MS = 8_000
_PAGE_CHANGE_POLL_MS = 400

_TABLE_ROWS_JS = """() => {
  const table = document.querySelector('table');
  if (!table) return null;
  return Array.from(table.querySelectorAll('tr')).map(tr =>
    Array.from(tr.querySelectorAll('th,td')).map(c => c.innerText.trim()));
}"""

_PCT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
_DAYS_RE = re.compile(r"(\d+)\s*dia", re.IGNORECASE)

# O que cada rotulo de cabecalho significa (texto normalizado, por conter):
#   NOME -> nome; RISCO -> risco; APLICACAO -> minimo; TAXA -> taxa maxima;
#   RESGATE -> prazo. Colunas de retorno ("12 MESES", "NO ANO", "NO MES")
#   ficam deliberadamente de fora: rentabilidade nao e um campo do FundOffer.
_HEADER_ROLES = (
    ("NOME", "name"),
    ("RISCO", "risk"),
    ("APLICACAO", "min_initial"),
    ("TAXA", "fee"),
    ("RESGATE", "redemption"),
)


def parse_pct(raw: object) -> float | None:
    """"0,45%" -> 0.45. O formato da coluna de taxa maxima."""
    if raw is None:
        return None
    match = _PCT_RE.search(str(raw))
    return float(match.group(1).replace(",", ".")) if match else None


def parse_redemption_days(raw: object) -> int | None:
    """"credito em conta em 1 dia util" -> 1; "no mesmo dia" -> 0."""
    if raw is None:
        return None
    text = normalize_text(str(raw))
    if not text:
        return None
    if "MESMO DIA" in text:
        return 0
    match = _DAYS_RE.search(text)
    return int(match.group(1)) if match else None


def _map_header(cells: list[str]) -> dict[str, int] | None:
    """Indice de cada papel na linha de cabecalho; None se nao for cabecalho."""
    mapping: dict[str, int] = {}
    for index, cell in enumerate(cells):
        normalized = normalize_text(cell)
        for token, role in _HEADER_ROLES:
            if role not in mapping and normalized.startswith(token):
                mapping[role] = index
                break
    return mapping if "name" in mapping else None


def offers_from_rentability_rows(pages: list[list[list[str]]]) -> list[FundOffer]:
    """Converte as tabelas renderizadas (uma por pagina) em FundOffers.

    A tabela nao traz CNPJ - a identidade fica pelo slug do nome, e o merge
    reconcilia com o Open Finance quando os nomes coincidem.
    """
    offers: list[FundOffer] = []
    seen: set[str] = set()

    for rows in pages or []:
        header: dict[str, int] | None = None
        for cells in rows or []:
            if not cells:
                continue
            candidate = _map_header(cells)
            if candidate:
                header = candidate
                continue
            if header is None or len(cells) <= header["name"]:
                continue

            name = cells[header["name"]].strip()
            if not name or len(name) < 4:
                continue

            def cell(role: str) -> str | None:
                index = header.get(role)
                return cells[index] if index is not None and index < len(cells) else None

            extra = {
                key: value
                for key, value in (
                    ("risco", cell("risk")),
                    ("resgate", cell("redemption")),
                )
                if value
            }

            offer = FundOffer(
                platform="itau",
                name=name,
                asset_class=classify_asset_class(name),
                min_initial=parse_money(cell("min_initial")),
                # "taxa maxima do produto" - mesma semantica do maxAdminFee do
                # Open Finance, entao entra no mesmo campo.
                admin_fee_pct=parse_pct(cell("fee")),
                redemption_payment_days=parse_redemption_days(cell("redemption")),
                extra=extra,
            )
            if offer.identity not in seen:
                seen.add(offer.identity)
                offers.append(offer)

    return offers


class ItauCollector(PlatformCollector):
    def _collect_playwright(self) -> list[FundOffer]:
        from shelf_monitor.collectors.browser import collect_via_browser

        if not self.platform.shelf_url:
            raise SourceUnavailable("shelf_url nao configurada para o Itau")

        offers, urls = collect_via_browser(
            self.platform_id,
            self.platform.shelf_url,
            user_agent=self.config.user_agent,
            executable_path=self.config.chromium_path,
            debug_dir=self.config.debug_dir if self.config.browser_debug else None,
            headed=self.config.browser_headed,
            channel=self.config.browser_channel,
            on_page=_paginate_rentability_table,
            payload_mapper=_map_rentability_payload,
        )
        self.source_urls.extend(urls)
        return offers


def _first_data_cell(rows: list[list[str]] | None) -> str | None:
    """Primeira celula de dado - o sensor de que a pagina trocou apos o clique."""
    for cells in rows or []:
        if cells and _map_header(cells) is None and cells[0].strip():
            return cells[0]
    return None


def _paginate_rentability_table(page) -> list[tuple[str, Any]]:
    """Clica "proxima" ate o fim, acumulando a tabela de cada pagina."""
    pages: list[list[list[str]]] = []

    for _ in range(_MAX_TABLE_PAGES):
        rows = page.evaluate(_TABLE_ROWS_JS)
        if not rows:
            break
        pages.append(rows)

        arrow = page.query_selector(_NEXT_SELECTOR)
        if arrow is None or arrow.is_disabled():
            break

        previous = _first_data_cell(rows)
        arrow.click()

        waited = 0
        changed = False
        while waited < _PAGE_CHANGE_TIMEOUT_MS:
            page.wait_for_timeout(_PAGE_CHANGE_POLL_MS)
            waited += _PAGE_CHANGE_POLL_MS
            if _first_data_cell(page.evaluate(_TABLE_ROWS_JS)) != previous:
                changed = True
                break
        if not changed:
            # Botao habilitado mas conteudo parado: fim de fato (ou pagina
            # travada) - parar aqui evita acumular a mesma pagina para sempre.
            break

    if not pages:
        return []
    return [(f"{page.url} (tabela de rentabilidade, {len(pages)} paginas)",
             {"itau_rentabilidade": pages})]


def _map_rentability_payload(url: str, payload: Any) -> list[FundOffer] | None:
    if isinstance(payload, dict) and "itau_rentabilidade" in payload:
        return offers_from_rentability_rows(payload["itau_rentabilidade"])
    return None  # demais payloads seguem pelo extrator generico
