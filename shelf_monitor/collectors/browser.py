"""Coleta de prateleiras que sao SPA, via Playwright headless.

XP, BTG e Itau renderizam a listagem no cliente: o HTML servido nao contem os
fundos, e duas delas devolvem 403 a um cliente HTTP simples. So um navegador de
verdade alcanca essas listas.

A captura tem tres vias, aplicadas em conjunto e nao em cascata:

1. **Trafego JSON** - o XHR que alimenta a listagem tem mais campos (e e mais
   estavel de parsear) do que o DOM. Guardamos *toda* resposta JSON, nao so a que
   o heuristico ja reconhece: quando a extracao falha, e esse material que permite
   escrever o parser certo em vez de adivinhar.
2. **Estado embutido** - `__NEXT_DATA__` e companhia.
3. **DOM renderizado** - linhas de tabela, cartoes repetidos e CNPJ no texto. Uma
   lista visivel na tela tem de ser alcancavel mesmo quando a API interna muda de
   forma.

Chromium ja vem instalado no ambiente (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers).
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from shelf_monitor.http import SourceUnavailable
from shelf_monitor.models import FundOffer
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    normalize_cvm_code,
    normalize_text,
    parse_int,
    parse_money,
    parse_number,
)

try:
    from playwright.sync_api import sync_playwright
    _PLAYWRIGHT_ERROR: str | None = None
except Exception as _exc:  # noqa: BLE001
    sync_playwright = None  # type: ignore[assignment]
    _PLAYWRIGHT_ERROR = str(_exc)

_NAV_TIMEOUT_MS = 60_000
_IDLE_TIMEOUT_MS = 20_000
# Teto para a espera por conteudo. A espera real termina assim que a lista
# aparece - o relogio so existe para o caso em que ela nunca aparece.
_CONTENT_TIMEOUT_MS = 25_000
_CONTENT_POLL_MS = 500
_SCROLL_ROUNDS = 20
_SCROLL_PAUSE_MS = 900

# Guardar todo JSON e util ate virar consumo de memoria: uma listagem grande
# passa de 10 MB e uma pagina pode disparar dezenas de XHR.
_MAX_JSON_RESPONSES = 120
_MAX_JSON_BYTES = 12 * 1024 * 1024

# Chaves que costumam carregar os campos de interesse, em varias grafias.
_NAME_KEYS = ("name", "nome", "fundname", "nomefundo", "displayname", "razaosocial")
_CNPJ_KEYS = ("cnpj", "cnpjnumber", "cnpjfundo", "documentnumber")
_CVM_CODE_KEYS = ("codigocvm", "cdcvm", "codcvm", "codigofundocvm", "cvmcode")
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
_URL_KEYS = ("url", "link", "detailurl", "slug", "permalink", "href")


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


def to_offer(platform_id: str, item: dict, base_url: str | None = None) -> FundOffer | None:
    """Mapeia um dict de fundo vindo de JSON arbitrario para FundOffer.

    A origem nao entra aqui: quem sabe de que fonte veio e o merge, que a
    registra em `sources` ao unir os resultados."""
    name = _as_text(_pick(item, _NAME_KEYS))
    if not name:
        return None

    category = _as_text(_pick(item, _CATEGORY_KEYS))
    closed_raw = _pick(item, _CLOSED_KEYS)
    closed = bool(closed_raw) if isinstance(closed_raw, bool) else None

    detail = _as_text(_pick(item, _URL_KEYS))
    if detail and base_url and "://" not in detail:
        detail = urljoin(base_url, detail)

    return FundOffer(
        platform=platform_id,
        name=name,
        cnpj=normalize_cnpj(_as_text(_pick(item, _CNPJ_KEYS))),
        # Sem _as_text: o codigo costuma vir como numero e normalize_cvm_code
        # aceita o tipo cru.
        cvm_code=normalize_cvm_code(_pick(item, _CVM_CODE_KEYS)),
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
        detail_url=detail if detail and "://" in detail else None,
    )


# --- extracao do DOM renderizado ---------------------------------------------

_CNPJ_RE = re.compile(r"\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}")
_MONEY_RE = re.compile(r"R\$\s*[\d][\d.\s]*(?:,\d{1,2})?")

# Tokens que aparecem em nome de fundo brasileiro. Um rotulo de navegacao
# ("Simule agora", "Renda fixa") nao passa nos tres testes juntos: token,
# numero minimo de palavras e denylist de termos genericos.
_FUND_TOKEN_RE = re.compile(
    r"(?<!\w)("
    r"FUNDO|FUNDOS|FI|FIC|FICFI|FIM|FIA|FIRF|FIDC|FII|FIP|ETF|"
    r"MULTIMERCADO|RENDA FIXA|ACOES|CAMBIAL|PREV|PREVIDEN\w*|REFERENCIADO|"
    r"CREDITO PRIVADO|MASTER|LONGO PRAZO|CURTO PRAZO|SELECTION|ADVISORY"
    r")(?!\w)"
)
_GENERIC_NAMES = {
    "FUNDO DE INVESTIMENTO", "FUNDOS DE INVESTIMENTO", "FUNDOS", "TODOS OS FUNDOS",
    "RENDA FIXA", "MULTIMERCADO", "ACOES", "FUNDOS DE ACOES", "CAMBIAL",
    "PREVIDENCIA", "FUNDOS IMOBILIARIOS", "FUNDOS DE RENDA FIXA",
    "FUNDOS MULTIMERCADO", "VER TODOS OS FUNDOS", "CONHECA OS FUNDOS",
}
_NAME_CLASS_RE = re.compile(r"nome|name|titulo|title|fund", re.IGNORECASE)
_CATEGORY_CLASS_RE = re.compile(r"categ|classe|class-|tipo|segment", re.IGNORECASE)

_MIN_CARDS = 3
_MAX_CARDS = 5_000
_MAX_CARD_CHARS = 1_200
_MAX_NAME_CHARS = 200
# Uma unica ocorrencia solta nao e prateleira: e rodape, banner ou exemplo.
_MIN_DOM_OFFERS = 2


def looks_like_fund_name(text: str | None, has_cnpj: bool = False) -> bool:
    """Um texto e nome de fundo? Com CNPJ ao lado, o criterio afrouxa.

    O CNPJ e evidencia forte por si so - se ele esta na mesma caixa, o texto ao
    lado e o nome do fundo mesmo que nao traga nenhum token conhecido.
    """
    if not text:
        return False
    stripped = " ".join(text.split())
    if not (6 <= len(stripped) <= _MAX_NAME_CHARS):
        return False

    normalized = normalize_text(stripped)
    if normalized in _GENERIC_NAMES:
        return False

    words = normalized.split()
    if not (2 <= len(words) <= 25):
        return False

    # Duas palavras de letras, no minimo. Sem isso o proprio CNPJ passaria por
    # nome: normalizado, "CNPJ: 12.345.678/0001-90" vira seis "palavras".
    letter_words = [w for w in words if sum(c.isalpha() for c in w) >= 2]
    if len(letter_words) < 2:
        return False

    if has_cnpj:
        return True
    return len(words) >= 3 and _FUND_TOKEN_RE.search(normalized) is not None


_CNPJ_LABEL_RE = re.compile(r"\bCNPJ\b[:\s.]*", re.IGNORECASE)


def _text_of(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _clean_name(text: str) -> str:
    """Tira o CNPJ e o rotulo dele de dentro do nome.

    Numa caixa sem marcacao ("Fundo X - CNPJ: 00.000.000/0001-00") o nome vem
    grudado no numero; guardar assim quebraria o slug que identifica o fundo
    quando ele aparece sem CNPJ em outra fonte.
    """
    cleaned = _CNPJ_LABEL_RE.sub(" ", _CNPJ_RE.sub(" ", text))
    return " ".join(cleaned.split()).strip(" -\u2013\u2014|,;:")


def _name_candidate(raw: str, has_cnpj: bool) -> str | None:
    """Limpa e valida um texto como nome de fundo."""
    cleaned = _clean_name(raw)
    return cleaned if looks_like_fund_name(cleaned, has_cnpj) else None


def _first_money(text: str) -> float | None:
    match = _MONEY_RE.search(text)
    return parse_money(match.group(0)) if match else None


def _offer_from_element(platform_id: str, element, base_url: str | None) -> FundOffer | None:
    """Le um cartao/linha renderizado. Devolve None quando nao parece um fundo."""
    text = _text_of(element)
    if not text or len(text) > _MAX_CARD_CHARS:
        return None

    cnpj_match = _CNPJ_RE.search(text)
    has_cnpj = cnpj_match is not None

    name: str | None = None
    # Um rotulo explicito ganha do palpite: classe com "nome"/"title" e o que o
    # proprio site diz ser o nome.
    for tag in element.find_all(attrs={"class": _NAME_CLASS_RE}):
        name = _name_candidate(_text_of(tag), has_cnpj)
        if name:
            break

    if not name:
        for tag in element.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b", "a"]):
            name = _name_candidate(_text_of(tag), has_cnpj)
            if name:
                break

    if not name:
        name = _name_candidate(text, has_cnpj)

    if not name:
        return None

    category = None
    for tag in element.find_all(attrs={"class": _CATEGORY_CLASS_RE}):
        candidate = _text_of(tag)
        if candidate and candidate != name and len(candidate) <= 60:
            category = candidate
            break

    link = element.find("a", href=True)
    detail_url = None
    if link and base_url:
        detail_url = urljoin(base_url, link["href"])
    elif link and "://" in link["href"]:
        detail_url = link["href"]

    return FundOffer(
        platform=platform_id,
        name=name,
        cnpj=normalize_cnpj(cnpj_match.group(0)) if cnpj_match else None,
        asset_class=classify_asset_class(category, name),
        raw_category=category,
        min_initial=_first_money(text),
        detail_url=detail_url,
    )


def _offers_from_tables(platform_id: str, soup, base_url: str | None) -> list[FundOffer]:
    """Linhas de tabela: o formato mais comum de lista de rentabilidade."""
    offers: list[FundOffer] = []
    for row in soup.find_all("tr"):
        cells = [_text_of(c) for c in row.find_all(["td", "th"])]
        cells = [c for c in cells if c]
        if len(cells) < 2:
            continue

        row_text = " ".join(cells)
        cnpj_match = _CNPJ_RE.search(row_text)
        has_cnpj = cnpj_match is not None

        name_index = next(
            (i for i, c in enumerate(cells) if _name_candidate(c, False)), None
        )
        if name_index is None and has_cnpj:
            # Com CNPJ na linha o criterio afrouxa, mas a coluna do proprio CNPJ
            # nao pode virar o nome.
            name_index = next(
                (
                    i
                    for i, c in enumerate(cells)
                    if not _CNPJ_RE.search(c) and _name_candidate(c, True)
                ),
                None,
            )
        if name_index is None:
            continue

        name = _clean_name(cells[name_index])
        # A categoria costuma vir na coluna seguinte. Colunas de numero, data ou
        # percentual (rentabilidade) nao servem de classe.
        category = next(
            (
                c
                for c in cells[name_index + 1 :]
                if c
                and "%" not in c
                and "R$" not in c
                and not _CNPJ_RE.search(c)
                and not re.fullmatch(r"[\d.,/\-\s]+", c)
                and len(c) <= 60
            ),
            None,
        )

        link = row.find("a", href=True)
        detail_url = urljoin(base_url, link["href"]) if link and base_url else None

        offers.append(
            FundOffer(
                platform=platform_id,
                name=name,
                cnpj=normalize_cnpj(cnpj_match.group(0)) if cnpj_match else None,
                asset_class=classify_asset_class(category, name),
                raw_category=category,
                # Nao se le taxa de tabela: numa lista de rentabilidade quase
                # todo "%" e retorno, e confundir os dois e pior que nao ter.
                min_initial=_first_money(row_text),
                detail_url=detail_url,
            )
        )
    return offers


def _offers_from_cards(platform_id: str, soup, base_url: str | None) -> list[FundOffer]:
    """Cartoes repetidos: elementos irmaos com a mesma assinatura de classe."""
    groups: dict[tuple[str, str], list] = defaultdict(list)
    for element in soup.find_all(True):
        classes = element.get("class")
        if not classes:
            continue
        groups[(element.name, " ".join(sorted(classes)))].append(element)

    offers: list[FundOffer] = []
    # Os grupos vem em ordem de documento, entao o cartao aparece antes do
    # titulo que mora dentro dele. Consumir os descendentes de um grupo aceito
    # evita ler o mesmo fundo duas vezes - uma pelo cartao (com CNPJ e minimo) e
    # outra pelo <h3> (sem nada), que nao se anulam porque tem chaves diferentes.
    consumed: set[int] = set()
    for elements in groups.values():
        if not (_MIN_CARDS <= len(elements) <= _MAX_CARDS):
            continue
        if any(id(element) in consumed for element in elements):
            continue
        hits = [
            offer
            for offer in (
                _offer_from_element(platform_id, element, base_url)
                for element in elements
            )
            if offer
        ]
        # Metade do grupo tem de parecer fundo. Um grupo em que so um item passa
        # e menu ou rodape com uma mencao solta, nao a listagem.
        if len(hits) < max(_MIN_CARDS, len(elements) // 2):
            continue

        offers.extend(hits)
        for element in elements:
            for child in element.descendants:
                if getattr(child, "name", None):
                    consumed.add(id(child))
    return offers


def _offers_from_cnpj_context(
    platform_id: str, soup, base_url: str | None
) -> list[FundOffer]:
    """CNPJ no texto: sobe do numero ate a caixa que contem o nome."""
    offers: list[FundOffer] = []
    seen_nodes: set[int] = set()

    for node in soup.find_all(string=_CNPJ_RE.search):
        element = node.parent
        for _ in range(5):
            if element is None or element.name in (None, "[document]", "html", "body"):
                break
            if id(element) in seen_nodes:
                break
            seen_nodes.add(id(element))
            offer = _offer_from_element(platform_id, element, base_url)
            if offer:
                offers.append(offer)
                break
            element = element.parent
    return offers


def extract_from_dom(
    platform_id: str, html: str, base_url: str | None = None
) -> list[FundOffer]:
    """Le a prateleira do HTML ja renderizado.

    Roda as tres leituras e une o resultado, exigindo de cada uma um minimo de
    ocorrencias: uma mencao isolada e ruido de rodape, nao prateleira.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")

    offers: list[FundOffer] = []
    seen: set[str] = set()
    for extractor in (_offers_from_tables, _offers_from_cards, _offers_from_cnpj_context):
        found = extractor(platform_id, soup, base_url)
        if len(found) < _MIN_DOM_OFFERS:
            continue
        for offer in found:
            if offer.identity not in seen:
                seen.add(offer.identity)
                offers.append(offer)
    return _consolidate(offers)


_RICHNESS_FIELDS = (
    "cnpj", "raw_category", "min_initial", "detail_url", "manager_name",
    "admin_fee_pct",
)


def _richness(offer: FundOffer) -> int:
    return sum(getattr(offer, field) is not None for field in _RICHNESS_FIELDS)


def _consolidate(offers: list[FundOffer]) -> list[FundOffer]:
    """Junta leituras do mesmo fundo vindas de caixas diferentes da pagina.

    A chave de identidade nao basta aqui: a mesma lista lida pela tabela (sem
    CNPJ) e pelo cartao (com CNPJ) gera duas chaves distintas para um fundo so.
    Agrupar pelo slug do nome resolve, mas dois CNPJs sob o mesmo slug sao dois
    fundos de verdade - master e feeder tem nomes quase iguais - e esses ficam.
    """
    from shelf_monitor.normalize import name_slug

    by_slug: dict[str, dict[str | None, FundOffer]] = {}
    for offer in offers:
        variants = by_slug.setdefault(name_slug(offer.name), {})
        current = variants.get(offer.cnpj)
        if current is None or _richness(offer) > _richness(current):
            variants[offer.cnpj] = offer

    consolidated: list[FundOffer] = []
    for variants in by_slug.values():
        identified = [o for cnpj, o in variants.items() if cnpj]
        # A versao sem CNPJ e o mesmo fundo visto por outra caixa da pagina.
        consolidated.extend(identified or variants.values())
    return consolidated


def _from_embedded_state(html: str) -> list[dict]:
    """Alguns sites embutem o estado inicial num <script> JSON."""
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


# --- renderizacao ------------------------------------------------------------


@dataclass
class CapturedResponse:
    """Uma resposta de rede vista pela pagina - o rastro que o diagnose salva."""

    url: str
    status: int
    content_type: str
    size: int
    resource_type: str
    is_json: bool
    # None = corpo nao foi tentado; False = tentou e falhou. Distinguir os dois
    # e o que diz se um payload sumiu por cap, por corpo indisponivel ou por
    # JSON invalido - tres causas com tres correcoes diferentes.
    body_read: bool | None = None
    json_ok: bool | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "status": self.status,
            "content_type": self.content_type,
            "size": self.size,
            "resource_type": self.resource_type,
            "is_json": self.is_json,
            "body_read": self.body_read,
            "json_ok": self.json_ok,
        }


@dataclass
class BrowserCapture:
    """Tudo o que o navegador viu numa pagina."""

    url: str
    final_url: str
    status: int | None
    html: str
    responses: list[CapturedResponse] = field(default_factory=list)
    payloads: list[tuple[str, Any]] = field(default_factory=list)
    # Respostas rotuladas como JSON cujo corpo nao parseou: url + primeiros
    # bytes. E a evidencia para reconhecer BOM, JSONP ou payload cifrado.
    invalid_payloads: list[tuple[str, bytes]] = field(default_factory=list)
    screenshot: bytes | None = None
    content_stats: dict = field(default_factory=dict)

    @property
    def json_responses(self) -> int:
        return sum(1 for r in self.responses if r.is_json)


_CONTENT_PROBE_JS = """() => {
  const text = document.body ? document.body.innerText : '';
  const cnpjs = text.match(/\\d{2}\\.\\d{3}\\.\\d{3}\\/\\d{4}-\\d{2}/g) || [];
  return {
    chars: text.length,
    cnpjs: cnpjs.length,
    rows: document.querySelectorAll('table tr').length,
    links: document.querySelectorAll('a').length,
  };
}"""


def _has_content(stats: dict) -> bool:
    """A lista ja apareceu? CNPJs ou linhas de tabela sao o sinal barato."""
    return stats.get("cnpjs", 0) >= 2 or stats.get("rows", 0) >= 5


def render_page(
    url: str,
    *,
    user_agent: str,
    storage_state: str | None = None,
    executable_path: str | None = None,
    screenshot: bool = False,
    block_assets: bool = True,
    nav_timeout_ms: int = _NAV_TIMEOUT_MS,
    headed: bool = False,
    channel: str | None = None,
    on_page=None,
) -> BrowserCapture:
    """Abre a pagina num Chromium real e devolve o que ele viu.

    A espera segue o conteudo, nao o relogio: apos `networkidle`, faz poll ate a
    lista aparecer (CNPJs ou linhas de tabela) e so entao rola a pagina, parando
    quando o texto para de crescer.

    `headed`/`channel` abrem um navegador com janela (ex.: o Chrome instalado do
    usuario, channel="chrome") - a via legitima para paginas que barram o
    Chromium headless. `on_page(page)` roda apos a rolagem, com a pagina viva:
    serve para paginar uma API interna com os cookies da sessao; o que devolver
    (lista de (url, payload)) entra em capture.payloads.
    """
    if sync_playwright is None:
        detail = f": {_PLAYWRIGHT_ERROR}" if _PLAYWRIGHT_ERROR else ""
        raise SourceUnavailable(
            f"playwright nao instalado ou com erro: pip install playwright{detail}"
        )

    responses: list[CapturedResponse] = []
    payloads: list[tuple[str, Any]] = []
    invalid_payloads: list[tuple[str, bytes]] = []
    # (resposta Playwright, registro) para ler o corpo DEPOIS, fora do handler.
    # Dentro do handler o corpo de uma resposta grande ainda esta chegando e o
    # body() falha - foi assim que o XHR de 1,5 MB do Itau se perdeu enquanto os
    # JSONs pequenos sobreviviam.
    pending_bodies: list[tuple[Any, CapturedResponse]] = []

    def on_response(response) -> None:
        try:
            headers = response.headers or {}
            content_type = headers.get("content-type", "")
            is_json = "json" in content_type.lower()

            record = CapturedResponse(
                url=response.url,
                status=response.status,
                content_type=content_type,
                size=_declared_size(headers),
                resource_type=getattr(response.request, "resource_type", ""),
                is_json=is_json,
            )
            responses.append(record)
            if is_json and len(pending_bodies) < _MAX_JSON_RESPONSES:
                pending_bodies.append((response, record))
        except Exception:
            pass

    def drain_bodies() -> None:
        """Le os corpos com a navegacao ja assentada, quando estao completos."""
        total = 0
        for response, record in pending_bodies:
            if total >= _MAX_JSON_BYTES:
                break
            try:
                body = response.body()
            except Exception:  # noqa: BLE001 - resposta descartada pelo browser
                record.body_read = False
                continue
            record.body_read = True
            record.size = len(body)
            total += len(body)
            try:
                payloads.append((response.url, json.loads(body.decode("utf-8-sig"))))
                record.json_ok = True
            except (ValueError, UnicodeDecodeError):
                record.json_ok = False
                invalid_payloads.append((response.url, bytes(body[:2048])))

    origin = "{0.scheme}://{0.netloc}/".format(urlsplit(url))

    try:
        with sync_playwright() as playwright:
            launch_args: dict[str, Any] = {"headless": not headed}
            if channel:
                # channel usa o navegador instalado na maquina (ex.: chrome);
                # nesse caso o executable_path do Chromium empacotado nao vale.
                launch_args["channel"] = channel
            elif executable_path:
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
                "timezone_id": "America/Sao_Paulo",
                "viewport": {"width": 1440, "height": 900},
                # Cabecalhos de navegador de verdade: e o que separa um 403 de um
                # 200 em boa parte dos filtros anti-bot dessas casas.
                "extra_http_headers": {
                    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Referer": origin,
                },
            }
            if storage_state:
                context_args["storage_state"] = storage_state
            context = browser.new_context(**context_args)

            if block_assets:
                # Imagem e fonte nao carregam dado nenhum e dominam o tempo de
                # carga numa pagina de banco.
                context.route(
                    "**/*",
                    lambda route: (
                        route.abort()
                        if route.request.resource_type in ("image", "media", "font")
                        else route.continue_()
                    ),
                )

            page = context.new_page()
            page.on("response", on_response)

            navigation = page.goto(
                url, timeout=nav_timeout_ms, wait_until="domcontentloaded"
            )
            status = navigation.status if navigation else None

            try:
                page.wait_for_load_state("networkidle", timeout=_IDLE_TIMEOUT_MS)
            except Exception:  # noqa: BLE001 - pagina com polling nunca fica idle
                pass

            stats = _wait_for_content(page)
            stats = _scroll_until_stable(page, stats)

            if on_page is not None:
                try:
                    extra = on_page(page)
                    if extra:
                        payloads.extend(extra)
                except Exception as exc:  # noqa: BLE001
                    # O gancho e um extra: a captura da pagina em si continua
                    # valendo mesmo que a paginacao por dentro falhe.
                    responses.append(
                        CapturedResponse(
                            url=f"on_page: {type(exc).__name__}: {exc}"[:200],
                            status=0,
                            content_type="",
                            size=0,
                            resource_type="hook",
                            is_json=False,
                        )
                    )

            drain_bodies()

            html = page.content()
            final_url = page.url
            shot = page.screenshot(full_page=False) if screenshot else None

            context.close()
            browser.close()
    except SourceUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        # As excecoes do Playwright trazem varias linhas de contexto do browser.
        detail = " ".join(str(exc).split())[:160]
        raise SourceUnavailable(f"navegacao falhou em {url}: {detail}") from exc

    return BrowserCapture(
        url=url,
        final_url=final_url,
        status=status,
        html=html,
        responses=responses,
        payloads=payloads,
        invalid_payloads=invalid_payloads,
        screenshot=shot,
        content_stats=stats,
    )


def _declared_size(headers: dict) -> int:
    try:
        return int(headers.get("content-length", 0))
    except (TypeError, ValueError):
        return 0


def _probe(page) -> dict:
    try:
        stats = page.evaluate(_CONTENT_PROBE_JS)
    except Exception:  # noqa: BLE001 - navegacao em curso invalida o contexto
        return {}
    return stats if isinstance(stats, dict) else {}


def _wait_for_content(page) -> dict:
    """Espera a lista aparecer, nao um numero de segundos."""
    waited = 0
    stats = _probe(page)
    while waited < _CONTENT_TIMEOUT_MS and not _has_content(stats):
        page.wait_for_timeout(_CONTENT_POLL_MS)
        waited += _CONTENT_POLL_MS
        stats = _probe(page)
    return stats


def _scroll_until_stable(page, stats: dict) -> dict:
    """Rola enquanto a pagina cresce - listagens carregam por scroll infinito."""
    previous = stats.get("chars", 0)
    for _ in range(_SCROLL_ROUNDS):
        page.mouse.wheel(0, 5000)
        page.wait_for_timeout(_SCROLL_PAUSE_MS)
        stats = _probe(page)
        current = stats.get("chars", 0)
        if current <= previous:
            break
        previous = current
    return stats


def collect_via_browser(
    platform_id: str,
    url: str,
    *,
    user_agent: str,
    storage_state: str | None = None,
    executable_path: str | None = None,
    debug_dir: Path | None = None,
    headed: bool = False,
    channel: str | None = None,
    on_page=None,
    payload_mapper=None,
    warnings: list[str] | None = None,
) -> tuple[list[FundOffer], list[str]]:
    """Renderiza a pagina e extrai os fundos por JSON, estado embutido e DOM.

    `payload_mapper(url, payload)` e a primeira chance de um coletor especifico
    mapear um payload cujo formato ele conhece (devolvendo a lista de offers);
    devolvendo None, o payload segue pelo extrator generico.

    Devolve (fundos, urls_de_origem). Lanca SourceUnavailable se o Playwright
    nao estiver instalado, a navegacao falhar ou nada for reconhecido.
    """
    capture = render_page(
        url,
        user_agent=user_agent,
        storage_state=storage_state,
        executable_path=executable_path,
        headed=headed,
        channel=channel,
        on_page=on_page,
    )

    if warnings is not None:
        for resp in capture.responses:
            if resp.resource_type == "hook":
                warnings.append(f"falha no gancho on_page: {resp.url}")

    if debug_dir is not None:
        dump_capture(debug_dir, platform_id, capture)

    offers: list[FundOffer] = []
    seen: set[str] = set()
    source_urls: list[str] = []

    def add_offers(found: list[FundOffer], origin: str) -> None:
        added = 0
        for offer in found:
            if offer and offer.identity not in seen:
                seen.add(offer.identity)
                offers.append(offer)
                added += 1
        if added:
            source_urls.append(origin)

    def absorb(items: list[dict], origin: str) -> None:
        add_offers(
            [to_offer(platform_id, item, capture.final_url) for item in items], origin
        )

    for response_url, payload in capture.payloads:
        if payload_mapper is not None:
            mapped = payload_mapper(response_url, payload)
            if mapped is not None:
                add_offers(mapped, response_url)
                continue
        absorb(extract_fund_dicts(payload), response_url)

    if not offers:
        absorb(_from_embedded_state(capture.html), f"{url} (estado embutido)")

    # O DOM entra sempre, nao so como plano B: um fundo visivel na tela e
    # ausente do XHR (destaque curado, por exemplo) e justamente o que
    # diferencia a prateleira comercial da lista bruta da API.
    dom_offers = extract_from_dom(platform_id, capture.html, capture.final_url)
    dom_added = 0
    for offer in dom_offers:
        if offer.identity not in seen:
            seen.add(offer.identity)
            offers.append(offer)
            dom_added += 1
    if dom_added:
        source_urls.append(f"{capture.final_url} (DOM renderizado)")

    if not offers:
        raise SourceUnavailable(
            f"nenhum fundo identificado em {url} "
            f"(HTTP {capture.status}, {len(capture.html)} bytes de HTML, "
            f"{capture.json_responses} respostas JSON, "
            f"{len(capture.payloads)} payloads lidos, "
            f"{capture.content_stats.get('cnpjs', 0)} CNPJs na tela). "
            "Rode `diagnose --browser` para gravar a evidencia."
        )

    return offers, source_urls or [capture.final_url]


def dump_capture(directory: Path, platform_id: str, capture: BrowserCapture) -> Path:
    """Grava HTML, XHRs, payloads e screenshot para inspecao posterior."""
    target = directory / platform_id
    target.mkdir(parents=True, exist_ok=True)

    (target / "pagina.html").write_text(capture.html, encoding="utf-8")
    (target / "requisicoes.json").write_text(
        json.dumps(
            {
                "url": capture.url,
                "final_url": capture.final_url,
                "status": capture.status,
                "content_stats": capture.content_stats,
                "responses": [r.to_dict() for r in capture.responses],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    payload_dir = target / "payloads"
    payload_dir.mkdir(exist_ok=True)
    for index, (response_url, payload) in enumerate(capture.payloads):
        path = payload_dir / f"{index:03d}.json"
        path.write_text(
            json.dumps(
                {"url": response_url, "payload": payload}, indent=2, ensure_ascii=False
            )
            + "\n",
            encoding="utf-8",
        )

    if capture.invalid_payloads:
        invalid_dir = target / "payloads-invalidos"
        invalid_dir.mkdir(exist_ok=True)
        for index, (response_url, preview) in enumerate(capture.invalid_payloads):
            (invalid_dir / f"{index:03d}.bin").write_bytes(
                response_url.encode("utf-8", "replace") + b"\n" + preview
            )

    if capture.screenshot:
        (target / "tela.png").write_bytes(capture.screenshot)

    return target
