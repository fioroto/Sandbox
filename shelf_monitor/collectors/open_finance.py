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
_MAX_PAGES = 200  # trava de seguranca contra paginacao mal comportada


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

    # --- descoberta de host ---------------------------------------------------

    def _load_directory(self) -> list[dict]:
        """Diretorio oficial de participantes, cacheado em disco."""
        cache_path = self.config.cache_dir / "participants.json"
        if cache_path.exists():
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
            if not any(brand.strip() and brand.strip() in org_name for brand in brands):
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

            total_pages = ((payload.get("meta") or {}).get("totalPages")) or 1
            if page >= int(total_pages) or not items:
                break
            page += 1

        return offers

    def _to_offer(self, item: dict) -> FundOffer | None:
        name = (item.get("name") or "").strip()
        if not name:
            return None

        fees = item.get("fees") or {}
        conditions = item.get("generalConditions") or {}
        redemption = conditions.get("redemption") or {}
        manager = item.get("fundManager") or {}
        admin = item.get("admin") or {}
        category = item.get("anbimaCategory") or item.get("category")

        perf_raw = fees.get("performanceFee") or {}
        performance_fee = None
        if perf_raw:
            performance_fee = PerformanceFee(
                amount_pct=parse_number(perf_raw.get("amount")),
                benchmark=perf_raw.get("benchmark"),
                method=perf_raw.get("method"),
            )

        return FundOffer(
            platform=self.platform.platform_id,
            name=name,
            cnpj=normalize_cnpj(item.get("cnpjNumber")),
            isin=item.get("isinCode"),
            manager_name=manager.get("name"),
            manager_cnpj=normalize_cnpj(manager.get("cnpjNumber")),
            admin_name=admin.get("name"),
            asset_class=classify_asset_class(category, item.get("fundType"), name),
            raw_category=category,
            min_initial=parse_number(conditions.get("minimumAmount")),
            admin_fee_pct=parse_number(fees.get("maxAdminFee")),
            performance_fee=performance_fee,
            redemption_quotation_days=parse_int(redemption.get("quotationDays")),
            redemption_payment_days=parse_int(redemption.get("paymentDays")),
            grace_days=parse_int(redemption.get("graceDays")),
            taxation=item.get("taxation"),
            detail_url=None,
            extra={"source": "open-finance"},
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
