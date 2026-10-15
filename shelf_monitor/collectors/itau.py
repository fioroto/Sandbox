"""Coletor do Itau.

A lista publica e a de rentabilidade (`/investimentos/fundos/rentabilidade`).
A evidencia de 2026-08-31 (navegador com janela, na maquina do usuario) mostrou:

- cliente HTTP simples leva 403 da Akamai no documento, e o headless shell do
  Playwright tambem; o Chromium completo em --headless=new abre a pagina sem
  janela nenhuma (medido em 2026-09-19 - ver browser._NEW_HEADLESS_ARGS);
- a tabela e paginada em ~10 fundos por pagina (45 paginas na medicao, ~450
  fundos), com botao `data-testid="page__arrow-next"`;
- as colunas: nome, risco, aplicacao inicial, retornos (12m/ano/mes), taxa
  maxima do produto, resgate - o cabecalho nomeia cada uma.

A de 2026-10-14 (SHELF_BROWSER_DEBUG=1, data/cache/debug/itau/):

- a tabela e a mesma - 477 fundos em 48 paginas -, so o cabecalho perdeu o
  texto dos tooltips ("12 meses" em vez de "12 meses referente ao periodo") e o
  risco veio sem a palavra "risco";
- o dataset chega cifrado pelo gateway `apicd.cloud.itau.com.br/charon/...`
  (1,6 MB de base64, nao JSON), depois de um aperto de mao em `/iske` que
  repete ~230 vezes com `400 InvalidSession` mesmo quando da certo. A coleta de
  2026-09-28 (HTTP 200, 1.031 respostas JSON, nenhuma linha) tem a cara desse
  aperto de mao sem fechar: o dobro de respostas, e a tabela nunca carregou.
  Por isso o coletor espera a primeira linha de dado, recarrega a pagina uma
  vez se ela nao vier, e confere as linhas lidas contra o "exibindo 10 de 477
  resultados" da propria pagina;
- nem a tabela nem o XHR cifrado trazem CNPJ: ele vem pelo nome
  (shelf_monitor.cnpj_por_nome), resolvido antes da deduplicacao por
  identidade - sem CNPJ, a chave e o slug, e o slug junta fundos distintos
  ("Real Investor Ações" e "Real Investor Multimercado").

O coletor clica "proxima" ate o fim, le a tabela renderizada de cada pagina e
mapeia pelas colunas do cabecalho - nunca por posicao fixa.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from shelf_monitor.cnpj_por_nome import ResolvedorPorNome, ResumoPorNome
from shelf_monitor.collectors.platform import PlatformCollector
from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import classify_asset_class, normalize_text, parse_money
from shelf_monitor.texto import contagem, milhar

_NEXT_SELECTOR = '[data-testid="page__arrow-next"]'
# 48 paginas em 2026-10-14; folga para a prateleira crescer sem virar loop infinito.
_MAX_TABLE_PAGES = 150
_PAGE_CHANGE_TIMEOUT_MS = 8_000
_PAGE_CHANGE_POLL_MS = 400
# Espera pela primeira linha de dado, por tentativa (a carga e uma recarga).
# Em 2026-10-14 a tabela carregou na primeira tentativa; o teto e para o dia
# em que o aperto de mao do gateway demora.
_TABLE_WAIT_MS = 45_000
_TABLE_POLL_MS = 500
_TABLE_ATTEMPTS = 2
_RELOAD_TIMEOUT_MS = 60_000
# A marca da casa, que a vitrine as vezes omite do nome comercial.
_MARCAS = ("ITAU",)

_TABLE_ROWS_JS = """() => {
  const table = document.querySelector('table');
  if (!table) return null;
  return Array.from(table.querySelectorAll('tr')).map(tr =>
    Array.from(tr.querySelectorAll('th,td')).map(c => c.innerText.trim()));
}"""

# "exibindo 10 de 477 resultados" - o total que a propria pagina anuncia.
_TOTAL_JS = """() => {
  const text = document.body ? document.body.innerText : '';
  const match = text.match(/exibindo\\s+\\d+\\s+de\\s+([\\d.]+)\\s+resultado/i);
  return match ? match[1] : null;
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


def parse_total(raw: object) -> int | None:
    """"477" ou "1.234" (o total do "exibindo 10 de N resultados") -> int."""
    digits = re.sub(r"\D", "", str(raw or ""))
    return int(digits) if digits else None


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


def _data_rows(
    pages: list[list[list[str]]],
) -> Iterator[tuple[dict[str, int], list[str]]]:
    """(cabecalho, celulas) de cada linha de dado com nome, pagina a pagina."""
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
            if name and len(name) >= 4:
                yield header, cells


def count_data_rows(pages: list[list[list[str]]]) -> int:
    """Linhas de fundo lidas, com repeticao - o que se compara ao total da pagina."""
    return sum(1 for _ in _data_rows(pages))


def offers_from_rentability_rows(pages: list[list[list[str]]]) -> list[FundOffer]:
    """Converte as tabelas renderizadas (uma por pagina) em FundOffers.

    A tabela nao traz CNPJ (ver shelf_monitor.cnpj_por_nome). A deduplicacao e
    pelo nome exato, nao pela identidade: sem CNPJ a identidade e o slug, e o
    slug junta fundos distintos - 8 dos 477 em 2026-10-14 ("Itaú Seleção
    Multifundos Renda Fixa" e "... Multimercado" viram ambos
    `itau-selecao-multifundos`).
    """
    offers: list[FundOffer] = []
    seen: set[str] = set()

    for header, cells in _data_rows(pages):
        name = cells[header["name"]].strip()
        normalizado = normalize_text(name)
        if normalizado in seen:
            continue
        seen.add(normalizado)

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

        offers.append(
            FundOffer(
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
        )

    return offers


@dataclass
class LeituraDaTabela:
    """O que a tabela rendeu nesta coleta: o mapper grava, o coletor le.

    O mapper roda dentro de `collect_via_browser`, que deduplica por identidade
    logo depois - por isso o CNPJ pelo nome e resolvido aqui, e nao depois.
    """

    market_dir: Path
    catalogo: list[FundOffer] = field(default_factory=list)
    lida: bool = False
    ofertas: list[FundOffer] = field(default_factory=list)
    # Linhas de fundo lidas (com repeticao) e o total que a pagina anuncia.
    linhas: int = 0
    total: int | None = None
    tentativas: int = 0
    resumo: ResumoPorNome | None = None

    def mapear(self, url: str, payload: Any) -> list[FundOffer] | None:
        if not (isinstance(payload, dict) and "itau_rentabilidade" in payload):
            return None  # demais payloads seguem pelo extrator generico
        pages = payload["itau_rentabilidade"] or []
        self.lida = True
        self.linhas = count_data_rows(pages)
        self.total = parse_total(payload.get("itau_total"))
        self.tentativas = int(payload.get("itau_tentativas") or 0)
        self.ofertas = offers_from_rentability_rows(pages)
        if self.ofertas:
            resolvedor = ResolvedorPorNome.carregar(
                self.market_dir, self.catalogo, marcas=_MARCAS
            )
            self.resumo = resolvedor.resolver(self.ofertas)
        return self.ofertas

    @property
    def incompleta(self) -> bool:
        return bool(self.total) and self.linhas < self.total

    def motivo_da_falha(self) -> str:
        return (
            "a tabela de rentabilidade não carregou "
            f"({contagem(self.tentativas or _TABLE_ATTEMPTS, 'tentativa', 'tentativas')}"
            f" de {_TABLE_WAIT_MS // 1000} s; o gateway da página não entregou os dados)"
        )


class ItauCollector(PlatformCollector):
    def __init__(self, config, platform, stop_at_first_source: bool = False):
        super().__init__(config, platform, stop_at_first_source=stop_at_first_source)
        # O catalogo Open Finance desta coleta - onde o nome da vitrine procura
        # o CNPJ primeiro. A estrategia OPEN_FINANCE roda antes da PLAYWRIGHT.
        self._catalogo: list[FundOffer] = []

    def _collect_open_finance(self) -> list[FundOffer]:
        funds = super()._collect_open_finance()
        self._catalogo = list(funds)
        return funds

    def _collect_playwright(self) -> list[FundOffer]:
        from shelf_monitor.collectors.browser import collect_via_browser

        if not self.platform.shelf_url:
            raise SourceUnavailable("shelf_url não configurada para o Itaú")

        leitura = LeituraDaTabela(self.config.market_dir, self._catalogo)
        try:
            offers, urls = collect_via_browser(
                self.platform_id,
                self.platform.shelf_url,
                user_agent=self.config.user_agent,
                executable_path=self.config.chromium_path,
                debug_dir=self.config.debug_dir if self.config.browser_debug else None,
                headed=self.config.browser_headed,
                channel=self.config.browser_channel,
                on_page=_paginate_rentability_table,
                payload_mapper=leitura.mapear,
                warnings=self.warnings,
            )
        except SourceUnavailable as exc:
            if leitura.lida and not leitura.ofertas:
                raise SourceUnavailable(f"{leitura.motivo_da_falha()} | {exc}") from exc
            raise

        if leitura.ofertas:
            # A tabela e a vitrine. O DOM da ultima pagina so rele 10 linhas
            # dela, sem CNPJ - e com o CNPJ resolvido elas ja nao colidem pela
            # identidade com as da tabela.
            da_tabela = {id(offer) for offer in leitura.ofertas}
            offers = [offer for offer in offers if id(offer) in da_tabela]
            urls = [url for url in urls if not url.endswith("(DOM renderizado)")]
            self._anotar(leitura, len(offers))
        self.source_urls.extend(urls)
        return offers

    def _anotar(self, leitura: LeituraDaTabela, mantidas: int) -> None:
        """Avisos (rebaixam para parcial) e observacoes da leitura da tabela."""
        if leitura.incompleta:
            self.warnings.append(
                f"tabela de rentabilidade incompleta: {leitura.linhas} de "
                f"{leitura.total} linhas lidas"
            )
        notas: list[str] = []
        if leitura.tentativas > 1:
            notas.append("a tabela de rentabilidade só carregou depois de recarregar a página")
        if leitura.resumo is not None:
            notas.append(leitura.resumo.nota())
        colapsadas = len(leitura.ofertas) - mantidas
        if colapsadas == 1:
            notas.append(
                "1 fundo sem CNPJ some na deduplicação (o slug dele é o de outro "
                "fundo da tabela)"
            )
        elif colapsadas:
            notas.append(
                f"{milhar(colapsadas)} fundos sem CNPJ somem na deduplicação (o slug "
                "deles é o de outros fundos da tabela)"
            )
        for nota in notas:
            self.partial_reason = (
                f"{self.partial_reason} | {nota}" if self.partial_reason else nota
            )


def _first_data_cell(rows: list[list[str]] | None) -> str | None:
    """Primeira celula de dado - o sensor de que a pagina trocou apos o clique."""
    for cells in rows or []:
        if cells and _map_header(cells) is None and cells[0].strip():
            return cells[0]
    return None


def _read_rows(page) -> list[list[str]] | None:
    try:
        return page.evaluate(_TABLE_ROWS_JS)
    except Exception:  # noqa: BLE001 - navegacao em curso invalida o contexto
        return None


def _wait_for_table(page) -> tuple[list[list[str]] | None, int]:
    """Espera a primeira linha de dado; recarrega a pagina uma vez se nao vier.

    Devolve (linhas da primeira pagina, tentativas usadas).
    """
    for attempt in range(1, _TABLE_ATTEMPTS + 1):
        if attempt > 1:
            try:
                page.reload(wait_until="domcontentloaded", timeout=_RELOAD_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - sem recarga, sem segunda chance
                return None, attempt
        waited = 0
        while True:
            rows = _read_rows(page)
            if _first_data_cell(rows) is not None:
                return rows, attempt
            if waited >= _TABLE_WAIT_MS:
                break
            page.wait_for_timeout(_TABLE_POLL_MS)
            waited += _TABLE_POLL_MS
    return None, _TABLE_ATTEMPTS


def _read_total(page) -> str | None:
    try:
        return page.evaluate(_TOTAL_JS)
    except Exception:  # noqa: BLE001
        return None


def _paginate_rentability_table(page) -> list[tuple[str, Any]]:
    """Clica "proxima" ate o fim, acumulando a tabela de cada pagina.

    Devolve o payload mesmo sem nenhuma linha: o coletor precisa saber que a
    tabela nao carregou, e nao so que nada foi reconhecido.
    """
    rows, attempts = _wait_for_table(page)
    total = _read_total(page)
    pages: list[list[list[str]]] = []

    for _ in range(_MAX_TABLE_PAGES):
        if not rows:
            break
        pages.append(rows)

        try:
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
        except Exception:  # noqa: BLE001
            break
        rows = _read_rows(page)

    return [(
        f"{page.url} (tabela de rentabilidade, {contagem(len(pages), 'página', 'páginas')})",
        {
            "itau_rentabilidade": pages,
            "itau_total": total,
            "itau_tentativas": attempts,
        },
    )]
