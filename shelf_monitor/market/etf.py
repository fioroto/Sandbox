"""Catalogação e identificação dos ETFs (Exchange Traded Funds) cadastrados na CVM.

O ETF tem um tipo estrutural próprio no cadastro da CVM - FIIM (Fundo de
Investimento em Índice de Mercado) -, tanto em `Tipo_Fundo` (registro legado)
quanto em `Tipo_Classe` (RCVM 175, onde a classe traz "Classes de Cotas de
Fundos FIIM"). É esse tipo, não o nome comercial, que decide se um registro é
ETF: muita gestora batiza um FIF comum de "XPTO ETF FIC FIF" porque ele apenas
replica um ETF (é o feeder dele), e o nome abreviado de um ETF de verdade nem
sempre traz "ETF" ou "Índice" ("BTGP B3 IBOVESPA F DE INDICE"). Catalogar pelo
nome, como a v1 fazia, produzia as duas falhas ao mesmo tempo: feeders entrando
como se fossem o ETF, e ETFs de nome abreviado ficando de fora.

O feeder ainda importa - é o que a distribuição de varejo empurra -, mas nunca
soma com o ETF que ele replica: contar os dois juntos duplicaria o mesmo
dinheiro. Por isso todo registro sai com um `kind` (`etf` ou `feeder`) explícito.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from shelf_monitor.config import Config
from shelf_monitor.market.importer import import_market
from shelf_monitor.market.models import MarketDatabase, Origin
from shelf_monitor.normalize import normalize_cnpj, normalize_text

_TICKER_PATTERN = re.compile(r"\b([A-Z]{4}11[B]?)\b", re.IGNORECASE)

# Fallback para o cadastro legado (cad_fi), que não tem Tipo_Classe - e para
# marcar feeders (FIF que replica um ETF, mas não é um). Nunca é a via
# principal de detecção do ETF em si: essa é o tipo FIIM.
_ETF_KEYWORDS = (
    "FUNDO DE INDICE",
    "FUNDOS DE INDICE",
    "INDEX FUND",
    "EXCHANGE TRADED FUND",
)

_CANCELLED_MARKER = "CANCELADO"


def extract_ticker(name: str | None) -> str | None:
    """Tenta extrair o código de negociação em bolsa (ex: BOVA11, HASH11, IVVB11).

    Só funciona quando o próprio cadastro escreve o ticker entre parênteses no
    nome - a maioria dos ETFs (a família "IT NOW", por exemplo) não faz isso.
    É por isso que `build_etf_catalog` também aceita um override manual por
    CNPJ (`data/etf/tickers.csv`).
    """
    if not name:
        return None
    match = _TICKER_PATTERN.search(name)
    if match:
        return match.group(1).upper()
    return None


def _is_fiim_type(fund_type: str | None, class_type: str | None) -> bool:
    """FIIM em `Tipo_Fundo` ou `Tipo_Classe` - o sinal estrutural do ETF."""
    for value in (fund_type, class_type):
        if value and "FIIM" in value.upper():
            return True
    return False


def classify_etf_kind(
    fund_type: str | None = None,
    class_type: str | None = None,
    cvm_class: str | None = None,
    anbima_class: str | None = None,
    name: str | None = None,
) -> str | None:
    """Decide se o registro é um ETF (`"etf"`), um feeder que replica um ETF
    (`"feeder"`) ou nenhum dos dois (`None`).

    Tipo FIIM é a via principal e não depende do nome. Palavra-chave no nome
    ou na classificação é o fallback - único sinal disponível no cadastro
    legado (sem Tipo_Classe) - e é o que marca um FIF como feeder.
    """
    if _is_fiim_type(fund_type, class_type):
        return "etf"

    targets = [
        normalize_text(t)
        for t in (fund_type, class_type, cvm_class, anbima_class, name)
        if t
    ]
    if not targets:
        return None

    for target in targets:
        if any(kw in target for kw in _ETF_KEYWORDS):
            return "feeder"
        # Casamento por palavra inteira 'ETF' para evitar falsos positivos.
        if re.search(r"\bETF\b", target):
            return "feeder"

    return None


def is_etf_record(
    fund_type: str | None = None,
    class_type: str | None = None,
    cvm_class: str | None = None,
    anbima_class: str | None = None,
    name: str | None = None,
) -> bool:
    """Compatibilidade: True para ETF ou feeder. Prefira `classify_etf_kind`
    quando a distinção entre os dois importar."""
    return (
        classify_etf_kind(fund_type, class_type, cvm_class, anbima_class, name)
        is not None
    )


# Ordem de teste importa: um benchmark de cripto batizado com "índice" no nome
# não pode cair em Ações Brasil por examinar o nome primeiro. O benchmark
# (`Indicador_Desempenho`) é sempre testado antes do nome/classificação -
# é o campo que diz o que o ETF replica, não como a gestora o vendeu.
_CATEGORY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Cripto",
        ("CRIPTO", "BITCOIN", "ETHER", "SOLANA", "HASH", "WEB3", "BLOCKCHAIN", "CRYPTO", "DEFI"),
    ),
    (
        "Renda Fixa",
        (
            "RENDA FIXA", "TITULOS PUBLICOS", "DEBENTURES", "IMPLICITA", "IPCA",
            "CDI", "TREASURY", "IMA-B", "IMA B", "IRF-M", "IRFM", "IRF M", "IDA",
            "IMA-S", "IMAS", "LFT", "SELIC", "NTN",
        ),
    ),
    (
        "Internacional",
        (
            "EXTERIOR", "INTERNACIONAL", "GLOBAL", "EUA", "S P 500", "SP500",
            "S P500", "SPX", "NASDAQ", "CHINA", "WORLD", "OFFSHORE", "MSCI",
            "EUROPA", "BDR",
        ),
    ),
    (
        "Ações Brasil",
        (
            "ACOES", "IBOVESPA", "IBOV", "IBRX", "SMLL", "SMALL CAP",
            "DIVIDENDOS", "IDIV", "PIBB", "INDICE BRASIL", "IFNC", "IMAT",
            "ISE", "IGCT", "ICO2",
        ),
    ),
    (
        "Multimercado",
        ("MULTIMERCADO", "COMMODITY", "COMMODITIES", "OURO", "GOLD"),
    ),
)


@lru_cache(maxsize=None)
def _keyword_pattern(keyword: str) -> re.Pattern[str]:
    """Regex de uma keyword de categoria, ancorada no INICIO de palavra.

    Busca por substring solta classificava errado em escala: "IDA" (indice de
    debentures ANBIMA) casa dentro de "RESPONSABILIDADE", e desde a RCVM 175
    toda classe se chama "... RESPONSABILIDADE LIMITADA" - 166 dos 266 registros
    do catalogo. O resultado era 175 "Renda Fixa", entre eles ETFs de Ibovespa.

    A ancora e so a da esquerda, de proposito: os codigos de indice aparecem
    colados a um sufixo que faz parte do nome do indice ("IMA-B5", "IBOV11"),
    e exigir fronteira a direita perderia justamente esses.
    """
    return re.compile(r"(?<!\w)" + re.escape(keyword))


def _matches_keyword(keyword: str, text: str) -> bool:
    return _keyword_pattern(keyword).search(text) is not None


def categorize_etf(
    cvm_class: str | None = None,
    anbima_class: str | None = None,
    name: str | None = None,
    benchmark: str | None = None,
) -> str:
    """Classifica o ETF numa categoria de alto nível para análise de fluxo.

    O benchmark (o que o ETF replica) decide antes do nome comercial ou da
    classificação CVM/ANBIMA - são o dado mais confiável para isso.
    """
    benchmark_text = normalize_text(benchmark)
    rest_text = normalize_text(f"{cvm_class or ''} {anbima_class or ''} {name or ''}")

    for label, keywords in _CATEGORY_KEYWORDS:
        if any(_matches_keyword(kw, benchmark_text) for kw in keywords):
            return label

    for label, keywords in _CATEGORY_KEYWORDS:
        if any(_matches_keyword(kw, rest_text) for kw in keywords):
            return label

    return "Outros"


@dataclass
class EtfMetadata:
    """Metadados de um ETF (ou feeder) cadastrado na CVM."""

    cnpj: str
    name: str
    kind: str = "etf"  # "etf" ou "feeder" - ver classify_etf_kind
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

    @property
    def is_etf(self) -> bool:
        return self.kind == "etf"


def _load_ticker_overrides(path: Path) -> dict[str, str]:
    """Override de ticker por CNPJ (`data/etf/tickers.csv`), quando existir.

    O cadastro da CVM não traz o código de negociação em bolsa; a maioria dos
    ETFs também não o escreve no nome (só `extract_ticker` consegue algo como
    "IT NOW IBOVESPA (IBOV11)"). Este arquivo existe para ser preenchido à mão
    ou por uma coleta futura na B3 - fica vazio por padrão, de propósito: um
    CNPJ↔ticker errado aqui contaminaria toda a categorização por ticker rio
    abaixo, e não há como validar essa lista sem acesso à fonte.
    """
    if not path.exists():
        return {}
    overrides: dict[str, str] = {}
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            cnpj = normalize_cnpj(row.get("cnpj"))
            ticker = (row.get("ticker") or "").strip().upper()
            if cnpj and ticker:
                overrides[cnpj] = ticker
    return overrides


def build_etf_catalog(
    config: Config,
    database: MarketDatabase | None = None,
    force_import: bool = False,
) -> dict[str, EtfMetadata]:
    """Constrói o mapa CNPJ -> EtfMetadata de todos os ETFs (e feeders)
    cadastrados na CVM.

    CNPJs de classe (RCVM 175) e CNPJs de fundo (legado ou classe única) são
    unificados. Registros cancelados ficam fora: um ETF cancelado não tem
    fluxo a monitorar e polui contagem de "quantos ETFs existem hoje".
    """
    if database is None:
        raw_zip = config.market_raw_dir / "registro_fundo_classe.zip"
        local_dir = config.market_raw_dir if raw_zip.exists() and not force_import else None
        database = import_market(config, local_dir=local_dir, force=force_import, skip_fees=True)

    ticker_overrides = _load_ticker_overrides(config.etf_tickers_path)

    def is_cancelled(situation: str | None) -> bool:
        return _CANCELLED_MARKER in normalize_text(situation)

    catalog: dict[str, EtfMetadata] = {}

    # Indexar fundos por registro_id / cnpj
    funds_by_id = {f.registro_id: f for f in database.funds}

    # 1. Processar classes (RCVM 175)
    for cls in database.classes:
        if not cls.cnpj:
            continue
        fund = funds_by_id.get(cls.fundo_id)
        fund_type = fund.fund_type if fund else None

        # O nome descritivo às vezes mora no fundo, não na classe - um fundo de
        # classe única frequentemente batiza a classe só de "CLASSE UNICA" e
        # deixa o nome comercial (com o "ETF" que denuncia o feeder) no
        # registro do fundo. `or` aqui não arrisca nada: o tipo estrutural
        # (FIIM) já decide sozinho na primeira chamada, sem depender de nome.
        kind = classify_etf_kind(
            fund_type=fund_type,
            class_type=cls.class_type,
            cvm_class=cls.cvm_classification,
            anbima_class=cls.anbima_classification,
            name=cls.name,
        ) or classify_etf_kind(
            fund_type=fund_type,
            class_type=cls.class_type,
            name=fund.name if fund else None,
        )
        if kind is None:
            continue

        situation = cls.situation or (fund.situation if fund else None)
        if is_cancelled(situation):
            continue

        cnpj_norm = normalize_cnpj(cls.cnpj)
        if not cnpj_norm:
            continue

        ticker = ticker_overrides.get(cnpj_norm) or extract_ticker(cls.name) or (
            extract_ticker(fund.name) if fund else None
        )
        category = categorize_etf(
            cls.cvm_classification, cls.anbima_classification, cls.name, cls.benchmark
        )

        catalog[cnpj_norm] = EtfMetadata(
            cnpj=cnpj_norm,
            name=cls.name,
            kind=kind,
            ticker=ticker,
            category=category,
            origin=Origin.RCVM175,
            fund_type=fund_type or cls.class_type,
            cvm_classification=cls.cvm_classification,
            anbima_classification=cls.anbima_classification,
            benchmark=cls.benchmark,
            manager_name=fund.manager_name if fund else None,
            admin_name=cls.custodian_name or (fund.admin_name if fund else None),
            situation=situation,
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

        kind = classify_etf_kind(
            fund_type=fund.fund_type,
            cvm_class=cvm_cls,
            anbima_class=anbima_cls,
            name=fund.name,
        )
        if kind is None:
            continue
        if is_cancelled(fund.situation):
            continue

        ticker = ticker_overrides.get(cnpj_norm) or extract_ticker(fund.name)
        category = categorize_etf(cvm_cls, anbima_cls, fund.name)

        catalog[cnpj_norm] = EtfMetadata(
            cnpj=cnpj_norm,
            name=fund.name,
            kind=kind,
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


def catalog_counts(catalog: dict[str, EtfMetadata]) -> dict[str, int]:
    """Contagem por tipo e categoria - a linha de cobertura que faltava: sem
    ela, "o monitor não traz todos os ETFs" só se descobre olhando a planilha
    linha a linha.
    """
    etfs = [m for m in catalog.values() if m.is_etf]
    feeders = [m for m in catalog.values() if not m.is_etf]
    by_category: dict[str, int] = {}
    for m in etfs:
        by_category[m.category] = by_category.get(m.category, 0) + 1
    return {
        "etfs": len(etfs),
        "feeders": len(feeders),
        "por_categoria": by_category,
    }
