"""Catalogação e identificação dos ETFs (Exchange Traded Funds) cadastrados na CVM.

Identifica ETFs tanto em fundos adaptados à RCVM 175 quanto no cadastro legado,
extraindo ticker (ex: BOVA11), categoria (Ações, Renda Fixa, Cripto, Internacional)
e metadados cadastrais.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from shelf_monitor.config import Config
from shelf_monitor.market.importer import import_market
from shelf_monitor.market.models import MarketDatabase, Origin
from shelf_monitor.normalize import normalize_cnpj, normalize_text

_TICKER_PATTERN = re.compile(r"\b([A-Z]{4}11[B]?)\b", re.IGNORECASE)

_ETF_KEYWORDS = (
    "FUNDO DE INDICE",
    "FUNDOS DE INDICE",
    "INDEX FUND",
    "EXCHANGE TRADED FUND",
)


def extract_ticker(name: str | None) -> str | None:
    """Tenta extrair o código de negociação em bolsa (ex: BOVA11, HASH11, IVVB11)."""
    if not name:
        return None
    match = _TICKER_PATTERN.search(name)
    if match:
        return match.group(1).upper()
    return None


def is_etf_record(
    fund_type: str | None = None,
    class_type: str | None = None,
    cvm_class: str | None = None,
    anbima_class: str | None = None,
    name: str | None = None,
) -> bool:
    """Verifica se os metadados caracterizam um ETF / Fundo de Índice.

    Combina tipo de fundo, classificação CVM/ANBIMA e denominações.
    """
    targets = [
        normalize_text(t)
        for t in (fund_type, class_type, cvm_class, anbima_class, name)
        if t
    ]
    if not targets:
        return False

    for target in targets:
        if any(kw in target for kw in _ETF_KEYWORDS):
            return True
        # Casamento por palavra inteira 'ETF' para evitar falsos positivos
        if re.search(r"\bETF\b", target):
            return True

    return False


def categorize_etf(
    cvm_class: str | None = None,
    anbima_class: str | None = None,
    name: str | None = None,
) -> str:
    """Classifica o ETF numa categoria de alto nível para análise de fluxo."""
    text = normalize_text(f"{cvm_class or ''} {anbima_class or ''} {name or ''}")

    if any(k in text for k in ("CRIPTO", "BITCOIN", "ETHER", "SOLANA", "HASH", "WEB3", "BLOCKCHAIN")):
        return "Cripto"

    if any(k in text for k in ("RENDA FIXA", "TITULOS PUBLICOS", "DEBENTURES", "IMPLICITA", "IPCA", "CDI", "TREASURY")):
        return "Renda Fixa"

    if any(k in text for k in ("EXTERIOR", "INTERNACIONAL", "GLOBAL", "EUA", "SP 500", "NASDAQ", "CHINA", "WORLD", "OFFSHORE")):
        return "Internacional"

    if any(k in text for k in ("ACOES", "IBOVESPA", "SMLL", "DIVIDENDOS", "IDIV", "PIBB", "INDICE BRASIL")):
        return "Ações Brasil"

    if any(k in text for k in ("MULTIMERCADO", "COMMODITY", "COMMODITIES", "OURO", "GOLD")):
        return "Multimercado"

    return "Outros"


@dataclass
class EtfMetadata:
    """Metadados de um ETF cadastrado na CVM."""

    cnpj: str
    name: str
    ticker: str | None = None
    category: str = "Outros"
    origin: Origin = Origin.RCVM175
    fund_type: str | None = None
    cvm_classification: str | None = None
    anbima_classification: str | None = None
    benchmark: str | None = None
    manager_name: str | None = None
    admin_name: str | None = None
    situation: str | None = None
    registered_at: date | None = None
    net_worth: float | None = None

    @property
    def display_name(self) -> str:
        if self.ticker:
            return f"{self.ticker} - {self.name}"
        return self.name


def build_etf_catalog(
    config: Config,
    database: MarketDatabase | None = None,
    force_import: bool = False,
) -> dict[str, EtfMetadata]:
    """Constrói o mapa CNPJ -> EtfMetadata de todos os ETFs cadastrados na CVM.

    CNPJs de classe (RCVM 175) e CNPJs de fundo (legado ou classe única) são unificados.
    """
    if database is None:
        raw_zip = config.market_raw_dir / "registro_fundo_classe.zip"
        local_dir = config.market_raw_dir if raw_zip.exists() and not force_import else None
        database = import_market(config, local_dir=local_dir, force=force_import, skip_fees=True)

    catalog: dict[str, EtfMetadata] = {}

    # Indexar fundos por registro_id / cnpj
    funds_by_id = {f.registro_id: f for f in database.funds}

    # 1. Processar classes (RCVM 175)
    for cls in database.classes:
        if not cls.cnpj:
            continue
        fund = funds_by_id.get(cls.fundo_id)
        fund_type = fund.fund_type if fund else None

        if is_etf_record(
            fund_type=fund_type,
            class_type=cls.class_type,
            cvm_class=cls.cvm_classification,
            anbima_class=cls.anbima_classification,
            name=cls.name,
        ):
            cnpj_norm = normalize_cnpj(cls.cnpj)
            if not cnpj_norm:
                continue

            ticker = extract_ticker(cls.name) or (extract_ticker(fund.name) if fund else None)
            category = categorize_etf(cls.cvm_classification, cls.anbima_classification, cls.name)

            catalog[cnpj_norm] = EtfMetadata(
                cnpj=cnpj_norm,
                name=cls.name,
                ticker=ticker,
                category=category,
                origin=Origin.RCVM175,
                fund_type=fund_type or cls.class_type,
                cvm_classification=cls.cvm_classification,
                anbima_classification=cls.anbima_classification,
                benchmark=cls.benchmark,
                manager_name=fund.manager_name if fund else None,
                admin_name=cls.custodian_name or (fund.admin_name if fund else None),
                situation=cls.situation or (fund.situation if fund else None),
                registered_at=cls.registered_at or (fund.registered_at if fund else None),
                net_worth=cls.net_worth or (fund.net_worth if fund else None),
            )

    # 2. Processar fundos (legados ou sem classes registradas separadamente)
    for fund in database.funds:
        if not fund.cnpj:
            continue
        cnpj_norm = normalize_cnpj(fund.cnpj)
        if not cnpj_norm or cnpj_norm in catalog:
            continue

        cvm_cls = fund.legacy_fields.get("cvm_classification")
        anbima_cls = fund.legacy_fields.get("anbima_classification")

        if is_etf_record(
            fund_type=fund.fund_type,
            cvm_class=cvm_cls,
            anbima_class=anbima_cls,
            name=fund.name,
        ):
            ticker = extract_ticker(fund.name)
            category = categorize_etf(cvm_cls, anbima_cls, fund.name)

            catalog[cnpj_norm] = EtfMetadata(
                cnpj=cnpj_norm,
                name=fund.name,
                ticker=ticker,
                category=category,
                origin=fund.origin,
                fund_type=fund.fund_type,
                cvm_classification=cvm_cls,
                anbima_classification=anbima_cls,
                manager_name=fund.manager_name,
                admin_name=fund.admin_name,
                situation=fund.situation,
                registered_at=fund.registered_at,
                net_worth=fund.net_worth,
            )

    return catalog
