"""Coletor generico da API de Dados Abertos de Investimentos do Open Finance Brasil.

Endpoint publico, sem autenticacao, padronizado por regulacao:
    GET https://<host-participante>/open-banking/opendata-investments/v1/funds

O payload (schema InvestmentsFund, spec 1.0.1) traz CNPJ do fundo, gestor com
CNPJ, categoria ANBIMA, taxas, minimo e prazos de resgate - exatamente os campos
do FundOffer. Por isso e a primeira estrategia de todo coletor de plataforma.

Ressalva: a completude varia por instituicao. Quem publica pouco (ou nada) cai
para a estrategia seguinte da plataforma.
"""
from __future__ import annotations

import json
import re
import time

import requests

from shelf_monitor.config import (
    OPEN_FINANCE_FUNDS_PATH,
    Config,
    PlatformConfig,
)
from shelf_monitor.http import SourceUnavailable, get_json
from shelf_monitor.models import FundOffer, PerformanceFee
from shelf_monitor.normalize import (
    classify_asset_class,
    normalize_cnpj,
    normalize_text,
    parse_int,
    parse_number,
)

_PAGE_SIZE = 500
# Trava contra paginacao mal comportada (servidor que nunca decrementa). O teto
# alto e deliberado: o BTG publica ~9.950 fundos e ha instituicoes que ignoram o
# page-size pedido e devolvem 10 por pagina - com o teto antigo (200) a coleta
# saia pela metade, e saia como OK.
_MAX_PAGES = 2_000
_DIRECTORY_CACHE_TTL = 24 * 60 * 60  # o diretorio muda devagar, mas muda


def _brand_matches(brand: str, org_name: str) -> bool:
    """Casa a marca respeitando fronteira de palavra.

    Sem isso "XP" casaria dentro de "BANCO EXPRESSO" e o coletor da XP adotaria
    o endpoint de outra instituicao - com status OK e a prateleira errada.
    """
    if not brand or not org_name:
        return False
    return re.search(rf"(?<!\w){re.escape(brand)}(?!\w)", org_name) is not None


def _as_mapping(value: object) -> dict:
    """Devolve o dict, ou um vazio quando a fonte mandou outra coisa."""
    return value if isinstance(value, dict) else {}


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


class OpenFinanceFunds:
    """Descobre o host do participante e pagina o endpoint /funds."""

    def __init__(
        self,
        config: Config,
        platform: PlatformConfig,
        session: requests.Session,
    ):
        self.config = config
        self.platform = platform
        self.session = session
        self.source_urls: list[str] = []
        # Metricas da paginacao, lidas pelo diagnose e pelo aviso de truncamento.
        self.effective_page_size: int | None = None
        self.total_records: int | None = None
        self.pages_read = 0
        self.truncated = False
        self.warnings: list[str] = []

    # --- descoberta de host ---------------------------------------------------

    def _load_directory(self) -> list[dict]:
        """Diretorio oficial de participantes, cacheado em disco.

        O cache expira: uma instituicao que troca de host deixaria o coletor
        preso num endpoint morto ate alguem apagar o arquivo a mao.
        """
        cache_path = self.config.cache_dir / "participants.json"
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < _DIRECTORY_CACHE_TTL:
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except (ValueError, OSError):
                    pass  # cache corrompido: rebaixa para download

        data = get_json(self.session, self.config.directory_url, self.config)
        if not isinstance(data, list):
            raise SourceUnavailable("diretorio Open Finance nao retornou uma lista")

        self.config.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(data), encoding="utf-8")
        return data

    def _discover_base_url(self) -> str:
        """Encontra a base URL opendata-investments da marca no diretorio."""
        if self.platform.open_finance_base_url:
            return self.platform.open_finance_base_url.rstrip("/")

        brands = [normalize_text(b) for b in self.platform.open_finance_brands]
        brands = [b for b in brands if b]
        if not brands:
            raise SourceUnavailable(
                f"{self.platform.platform_id} nao tem marca Open Finance configurada"
            )

        participants = self._load_directory()
        self.source_urls.append(self.config.directory_url)

        for participant in participants:
            org_name = normalize_text(
                participant.get("OrganisationName")
                or participant.get("RegisteredName")
                or ""
            )
            if not any(_brand_matches(brand, org_name) for brand in brands):
                continue

            for server in participant.get("AuthorisationServers", []) or []:
                for resource in server.get("ApiResources", []) or []:
                    family = str(resource.get("ApiFamilyType", "")).lower()
                    if "investment" not in family:
                        continue
                    for endpoint in resource.get("ApiDiscoveryEndpoints", []) or []:
                        uri = endpoint.get("ApiEndpoint") or ""
                        if OPEN_FINANCE_FUNDS_PATH in uri:
                            return uri.split(OPEN_FINANCE_FUNDS_PATH)[0].rstrip("/")

        raise SourceUnavailable(
            f"nenhum endpoint opendata-investments encontrado para "
            f"{self.platform.platform_id} no diretorio Open Finance"
        )

    # --- coleta ---------------------------------------------------------------

    def fetch(self) -> list[FundOffer]:
        base_url = self._discover_base_url()
        url = f"{base_url}{OPEN_FINANCE_FUNDS_PATH}"
        self.source_urls.append(url)

        offers: list[FundOffer] = []
        page = 1
        total_pages = 1
        exhausted_early = False
        while page <= _MAX_PAGES:
            payload = get_json(
                self.session,
                url,
                self.config,
                params={"page": page, "page-size": _PAGE_SIZE},
            )
            if not isinstance(payload, dict):
                raise SourceUnavailable(f"payload inesperado em {url}")

            brand_data = payload.get("data") or []
            items = _flatten_funds(brand_data)
            for item in items:
                offer = self._to_offer(item)
                if offer:
                    offers.append(offer)

            meta = _as_mapping(payload.get("meta"))
            if page == 1:
                # Nem toda instituicao respeita o page-size pedido. Registrar o
                # efetivo e o que explica, depois, uma paginacao de 995 paginas
                # quando se pediu 500 itens por pagina.
                self.effective_page_size = len(items)
                self.total_records = parse_int(meta.get("totalRecords"))

            # O total nunca diminui: uma pagina do meio que omita `totalPages`
            # encerraria o laco como se a paginacao tivesse acabado, e as
            # paginas restantes sumiriam com o snapshot saindo OK.
            declared = parse_int(meta.get("totalPages"))
            if declared and declared > total_pages:
                total_pages = declared
            self.pages_read = page

            if not items:
                exhausted_early = page < total_pages
                break
            if page >= total_pages:
                break
            page += 1

        if total_pages > _MAX_PAGES:
            # Truncar em silencio faria o snapshot sair OK com metade da
            # prateleira - e o diff do dia seguinte leria isso como churn.
            self.truncated = True
            self.warnings.append(
                f"paginacao truncada em {_MAX_PAGES} paginas de {total_pages} "
                f"(page-size efetivo {self.effective_page_size}); "
                f"{len(offers)} de {self.total_records or '?'} fundos coletados"
            )
        elif exhausted_early and (
            self.total_records is None or len(offers) < self.total_records
        ):
            # Paginas acabaram antes do total anunciado: ou a instituicao mentiu
            # no `totalPages`, ou a listagem parou no meio. Nos dois casos a
            # prateleira coletada nao e a inteira.
            self.truncated = True
            self.warnings.append(
                f"paginacao terminou na pagina {self.pages_read} de {total_pages} "
                f"anunciadas; {len(offers)} de {self.total_records or '?'} "
                "fundos coletados"
            )

        return offers

    def _to_offer(self, item: dict) -> FundOffer | None:
        name = _as_text(item.get("name"))
        if not name:
            return None

        # A spec define objetos aqui, mas implementacoes divergem (listas,
        # strings, null). Um campo fora do formato vira campo ausente, nunca
        # uma excecao que derruba a coleta inteira da plataforma.
        fees = _as_mapping(item.get("fees"))
        conditions = _as_mapping(item.get("generalConditions"))
        redemption = _as_mapping(conditions.get("redemption"))
        manager = _as_mapping(item.get("fundManager"))
        admin = _as_mapping(item.get("admin"))
        category = _as_text(item.get("anbimaCategory")) or _as_text(item.get("category"))

        perf_raw = _as_mapping(fees.get("performanceFee"))
        performance_fee = None
        if perf_raw:
            performance_fee = PerformanceFee(
                amount_pct=parse_number(perf_raw.get("amount")),
                benchmark=_as_text(perf_raw.get("benchmark")),
                method=_as_text(perf_raw.get("method")),
            )

        return FundOffer(
            platform=self.platform.platform_id,
            name=name,
            cnpj=normalize_cnpj(_as_text(item.get("cnpjNumber"))),
            isin=_as_text(item.get("isinCode")),
            manager_name=_as_text(manager.get("name")),
            manager_cnpj=normalize_cnpj(_as_text(manager.get("cnpjNumber"))),
            admin_name=_as_text(admin.get("name")),
            asset_class=classify_asset_class(
                category, _as_text(item.get("fundType")), name
            ),
            raw_category=category,
            min_initial=parse_number(conditions.get("minimumAmount")),
            admin_fee_pct=parse_number(fees.get("maxAdminFee")),
            performance_fee=performance_fee,
            redemption_quotation_days=parse_int(redemption.get("quotationDays")),
            redemption_payment_days=parse_int(redemption.get("paymentDays")),
            grace_days=parse_int(redemption.get("graceDays")),
            taxation=_as_text(item.get("taxation")),
            detail_url=None,
        )


def _flatten_funds(data: object) -> list[dict]:
    """Extrai a lista de fundos do envelope, que varia entre implementacoes.

    A spec agrupa fundos por marca/participante, mas ha instituicoes que devolvem
    a lista direta. Aceita ambos em vez de quebrar.
    """
    items: list[dict] = []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return items

    for entry in data:
        if not isinstance(entry, dict):
            continue
        # Formato agrupado: brand -> companies[] -> funds[]
        companies = entry.get("companies")
        if isinstance(companies, list):
            for company in companies:
                if isinstance(company, dict):
                    for key in ("funds", "investmentFunds"):
                        nested = company.get(key)
                        if isinstance(nested, list):
                            items.extend(f for f in nested if isinstance(f, dict))
            continue
        for key in ("funds", "investmentFunds"):
            nested = entry.get(key)
            if isinstance(nested, list):
                items.extend(f for f in nested if isinstance(f, dict))
                break
        else:
            # Formato plano: o proprio item e um fundo
            if entry.get("name"):
                items.append(entry)

    return items
