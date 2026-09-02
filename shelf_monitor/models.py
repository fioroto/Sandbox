"""Modelos de dominio do monitor de prateleiras de fundos."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class CollectorStrategy(str, Enum):
    """Como um coletor obteve os dados da prateleira."""

    OPEN_FINANCE = "open-finance"
    API_JSON = "api-json"
    HTML_SCRAPE = "html-scrape"
    PLAYWRIGHT = "playwright"
    AUTHENTICATED = "authenticated"


class SnapshotStatus(str, Enum):
    """Qualidade da coleta - dirige a regra anti-falso-positivo do diff."""

    OK = "ok"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class AssetClass(str, Enum):
    """Taxonomia interna de classe de ativo, normalizada entre plataformas."""

    RENDA_FIXA = "renda_fixa"
    MULTIMERCADO = "multimercado"
    ACOES = "acoes"
    CAMBIAL = "cambial"
    INTERNACIONAL = "internacional"
    PREVIDENCIA = "previdencia"
    OUTROS = "outros"


@dataclass
class PerformanceFee:
    amount_pct: float | None = None
    benchmark: str | None = None
    method: str | None = None

    def to_dict(self) -> dict:
        return {
            "amount_pct": self.amount_pct,
            "benchmark": self.benchmark,
            "method": self.method,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> PerformanceFee | None:
        if not data:
            return None
        return cls(
            amount_pct=data.get("amount_pct"),
            benchmark=data.get("benchmark"),
            method=data.get("method"),
        )


# Fontes que representam a vitrine comercial da casa (o que o varejo ve),
# em oposicao ao catalogo regulatorio do Open Finance.
LISTING_SOURCES = frozenset(
    {
        CollectorStrategy.API_JSON.value,
        CollectorStrategy.PLAYWRIGHT.value,
        CollectorStrategy.HTML_SCRAPE.value,
        CollectorStrategy.AUTHENTICATED.value,
    }
)


@dataclass
class FundOffer:
    """Um fundo ofertado por uma plataforma, normalizado entre fontes."""

    platform: str
    name: str
    cnpj: str | None = None
    isin: str | None = None
    # Codigo CVM da subclasse, classe ou fundo, conforme o nivel que a casa
    # anuncia. E a chave de reserva para casar com o cadastro oficial quando a
    # fonte nao publica CNPJ.
    cvm_code: str | None = None
    manager_name: str | None = None
    manager_cnpj: str | None = None
    admin_name: str | None = None
    asset_class: AssetClass = AssetClass.OUTROS
    raw_category: str | None = None
    min_initial: float | None = None
    admin_fee_pct: float | None = None
    performance_fee: PerformanceFee | None = None
    redemption_quotation_days: int | None = None
    redemption_payment_days: int | None = None
    grace_days: int | None = None
    taxation: str | None = None
    closed_for_investment: bool | None = None
    detail_url: str | None = None
    # Todas as fontes em que este fundo apareceu. Um fundo presente no site mas
    # ausente do Open Finance (ou o inverso) e informacao competitiva, nao ruido.
    sources: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def in_source(self, source: str) -> bool:
        return source in self.sources

    @property
    def on_listing(self) -> bool:
        """O fundo esta na vitrine da casa - a lista que o varejo ve.

        Vitrine e o que veio do canal da propria casa (site, API do portal ou
        area logada). Open Finance nao conta: ele publica tudo que a casa
        administra, inclusive o que nunca aparece para o cliente.
        """
        return any(source in LISTING_SOURCES for source in self.sources)

    @property
    def identity(self) -> str:
        """Chave de comparacao entre snapshots: CNPJ quando existe, senao slug do nome."""
        from shelf_monitor.normalize import name_slug

        if self.cnpj:
            return self.cnpj
        return f"slug:{name_slug(self.name)}"

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "name": self.name,
            "cnpj": self.cnpj,
            "isin": self.isin,
            "cvm_code": self.cvm_code,
            "manager_name": self.manager_name,
            "manager_cnpj": self.manager_cnpj,
            "admin_name": self.admin_name,
            "asset_class": self.asset_class.value,
            "raw_category": self.raw_category,
            "min_initial": self.min_initial,
            "admin_fee_pct": self.admin_fee_pct,
            "performance_fee": (
                self.performance_fee.to_dict() if self.performance_fee else None
            ),
            "redemption_quotation_days": self.redemption_quotation_days,
            "redemption_payment_days": self.redemption_payment_days,
            "grace_days": self.grace_days,
            "taxation": self.taxation,
            "closed_for_investment": self.closed_for_investment,
            "detail_url": self.detail_url,
            "sources": self.sources,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict) -> FundOffer:
        return cls(
            platform=data["platform"],
            name=data["name"],
            cnpj=data.get("cnpj"),
            isin=data.get("isin"),
            # Snapshot anterior ao lookup por codigo nao tem a chave: le como
            # None e o historico continua comparavel.
            cvm_code=data.get("cvm_code"),
            manager_name=data.get("manager_name"),
            manager_cnpj=data.get("manager_cnpj"),
            admin_name=data.get("admin_name"),
            asset_class=AssetClass(data.get("asset_class", "outros")),
            raw_category=data.get("raw_category"),
            min_initial=data.get("min_initial"),
            admin_fee_pct=data.get("admin_fee_pct"),
            performance_fee=PerformanceFee.from_dict(data.get("performance_fee")),
            redemption_quotation_days=data.get("redemption_quotation_days"),
            redemption_payment_days=data.get("redemption_payment_days"),
            grace_days=data.get("grace_days"),
            taxation=data.get("taxation"),
            closed_for_investment=data.get("closed_for_investment"),
            detail_url=data.get("detail_url"),
            # Snapshots anteriores a coleta multi-fonte gravavam a origem em
            # extra["source"]; le-los aqui mantem o historico comparavel.
            sources=data.get("sources")
            or ([data["extra"]["source"]] if (data.get("extra") or {}).get("source") else []),
            extra=data.get("extra") or {},
        )


SNAPSHOT_SCHEMA_VERSION = 1


@dataclass
class ShelfSnapshot:
    """Foto da prateleira de uma plataforma num dado momento."""

    platform: str
    collected_at: datetime
    strategy: CollectorStrategy
    status: SnapshotStatus
    funds: list[FundOffer] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    error: str | None = None
    # Todas as estrategias que trouxeram fundos nesta coleta. `strategy` guarda
    # a principal (a de maior confianca), para leitura rapida e compatibilidade.
    strategies: list[str] = field(default_factory=list)
    # Quantos fundos cada estrategia trouxe, antes da uniao - mede a cobertura
    # real de cada fonte para aquela casa.
    source_counts: dict[str, int] = field(default_factory=dict)
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    @property
    def snapshot_date(self) -> date:
        return self.collected_at.date()

    @property
    def sources_used(self) -> list[str]:
        return self.strategies or ([self.strategy.value] if self.funds else [])

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "platform": self.platform,
            "collected_at": self.collected_at.isoformat(),
            "strategy": self.strategy.value,
            "strategies": self.strategies,
            "source_counts": self.source_counts,
            "status": self.status.value,
            "source_urls": self.source_urls,
            "error": self.error,
            "fund_count": len(self.funds),
            # ordenado por identity para diffs de git legiveis
            "funds": [f.to_dict() for f in sorted(self.funds, key=lambda x: x.identity)],
        }

    @classmethod
    def from_dict(cls, data: dict) -> ShelfSnapshot:
        return cls(
            platform=data["platform"],
            collected_at=datetime.fromisoformat(data["collected_at"]),
            strategy=CollectorStrategy(data["strategy"]),
            status=SnapshotStatus(data["status"]),
            funds=[FundOffer.from_dict(f) for f in data.get("funds", [])],
            source_urls=data.get("source_urls", []),
            error=data.get("error"),
            strategies=data.get("strategies", []),
            source_counts=data.get("source_counts", {}),
            schema_version=data.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
        )


@dataclass
class CvmFund:
    """Fundo registrado na CVM cujo gestor pertence a watchlist de uma casa."""

    cnpj: str
    name: str
    house: str
    matched_by: str
    fund_type: str | None = None
    manager_name: str | None = None
    manager_cnpj: str | None = None
    admin_name: str | None = None
    situation: str | None = None
    situation_started_at: str | None = None
    registered_at: str | None = None
    adapted_rcvm175_at: str | None = None
    cvm_classes: list[str] = field(default_factory=list)
    pl: float | None = None

    @property
    def is_pre_operational(self) -> bool:
        """Registrado na CVM mas ainda nao lancado - o sinal antecipado mais forte."""
        from shelf_monitor.normalize import strip_accents

        if not self.situation:
            return False
        return "PRE-OPERACIONAL" in strip_accents(self.situation).upper()

    def to_dict(self) -> dict:
        return {
            "cnpj": self.cnpj,
            "name": self.name,
            "house": self.house,
            "matched_by": self.matched_by,
            "fund_type": self.fund_type,
            "manager_name": self.manager_name,
            "manager_cnpj": self.manager_cnpj,
            "admin_name": self.admin_name,
            "situation": self.situation,
            "situation_started_at": self.situation_started_at,
            "registered_at": self.registered_at,
            "adapted_rcvm175_at": self.adapted_rcvm175_at,
            "cvm_classes": self.cvm_classes,
            "pl": self.pl,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CvmFund:
        return cls(
            cnpj=data["cnpj"],
            name=data["name"],
            house=data["house"],
            matched_by=data.get("matched_by", ""),
            fund_type=data.get("fund_type"),
            manager_name=data.get("manager_name"),
            manager_cnpj=data.get("manager_cnpj"),
            admin_name=data.get("admin_name"),
            situation=data.get("situation"),
            situation_started_at=data.get("situation_started_at"),
            registered_at=data.get("registered_at"),
            adapted_rcvm175_at=data.get("adapted_rcvm175_at"),
            cvm_classes=data.get("cvm_classes", []),
            pl=data.get("pl"),
        )


@dataclass
class CvmSnapshot:
    collected_at: datetime
    status: SnapshotStatus
    funds: list[CvmFund] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    error: str | None = None
    schema_version: int = SNAPSHOT_SCHEMA_VERSION

    @property
    def snapshot_date(self) -> date:
        return self.collected_at.date()

    def by_house(self) -> dict[str, list[CvmFund]]:
        out: dict[str, list[CvmFund]] = {}
        for fund in self.funds:
            out.setdefault(fund.house, []).append(fund)
        return out

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "collected_at": self.collected_at.isoformat(),
            "status": self.status.value,
            "source_urls": self.source_urls,
            "error": self.error,
            "fund_count": len(self.funds),
            "funds": [f.to_dict() for f in sorted(self.funds, key=lambda x: x.cnpj)],
        }

    @classmethod
    def from_dict(cls, data: dict) -> CvmSnapshot:
        return cls(
            collected_at=datetime.fromisoformat(data["collected_at"]),
            status=SnapshotStatus(data["status"]),
            funds=[CvmFund.from_dict(f) for f in data.get("funds", [])],
            source_urls=data.get("source_urls", []),
            error=data.get("error"),
            schema_version=data.get("schema_version", SNAPSHOT_SCHEMA_VERSION),
        )


@dataclass
class FieldChange:
    field: str
    old: object
    new: object

    def to_dict(self) -> dict:
        return {"field": self.field, "old": self.old, "new": self.new}


@dataclass
class FundChange:
    identity: str
    name: str
    changes: list[FieldChange] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "identity": self.identity,
            "name": self.name,
            "changes": [c.to_dict() for c in self.changes],
        }


@dataclass
class ShelfDiff:
    """Mudancas na prateleira de uma plataforma entre dois snapshots."""

    platform: str
    prev_date: date | None
    curr_date: date
    added: list[FundOffer] = field(default_factory=list)
    removed: list[FundOffer] = field(default_factory=list)
    changed: list[FundChange] = field(default_factory=list)
    suspect: bool = False
    suspect_reason: str | None = None

    @property
    def is_first_run(self) -> bool:
        return self.prev_date is None

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.changed)

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "prev_date": self.prev_date.isoformat() if self.prev_date else None,
            "curr_date": self.curr_date.isoformat(),
            "suspect": self.suspect,
            "suspect_reason": self.suspect_reason,
            "added": [f.to_dict() for f in self.added],
            "removed": [f.to_dict() for f in self.removed],
            "changed": [c.to_dict() for c in self.changed],
        }


@dataclass
class CvmSituationChange:
    fund: CvmFund
    old_situation: str | None
    new_situation: str | None

    @property
    def is_launch(self) -> bool:
        """Pre-operacional -> funcionamento normal = fundo entrou em operacao."""
        from shelf_monitor.normalize import strip_accents

        old = strip_accents(self.old_situation or "").upper()
        new = strip_accents(self.new_situation or "").upper()
        return "PRE-OPERACIONAL" in old and "FUNCIONAMENTO NORMAL" in new

    def to_dict(self) -> dict:
        return {
            "fund": self.fund.to_dict(),
            "old_situation": self.old_situation,
            "new_situation": self.new_situation,
            "is_launch": self.is_launch,
        }


@dataclass
class CvmDiff:
    prev_date: date | None
    curr_date: date
    new_registrations: list[CvmFund] = field(default_factory=list)
    situation_changes: list[CvmSituationChange] = field(default_factory=list)
    cancelled: list[CvmFund] = field(default_factory=list)
    suspect: bool = False
    suspect_reason: str | None = None

    @property
    def is_first_run(self) -> bool:
        return self.prev_date is None

    @property
    def has_changes(self) -> bool:
        return bool(self.new_registrations or self.situation_changes or self.cancelled)

    @property
    def launches(self) -> list[CvmSituationChange]:
        return [c for c in self.situation_changes if c.is_launch]

    def to_dict(self) -> dict:
        return {
            "prev_date": self.prev_date.isoformat() if self.prev_date else None,
            "curr_date": self.curr_date.isoformat(),
            "suspect": self.suspect,
            "suspect_reason": self.suspect_reason,
            "new_registrations": [f.to_dict() for f in self.new_registrations],
            "situation_changes": [c.to_dict() for c in self.situation_changes],
            "cancelled": [f.to_dict() for f in self.cancelled],
        }


@dataclass
class RunReport:
    """Tudo que uma execucao produziu - entrada dos renderers."""

    generated_at: datetime
    snapshots: list[ShelfSnapshot] = field(default_factory=list)
    shelf_diffs: list[ShelfDiff] = field(default_factory=list)
    cvm_snapshot: CvmSnapshot | None = None
    cvm_diff: CvmDiff | None = None
    # Cobertura do join com a base oficial (enrich.EnrichmentStats.to_dict());
    # None quando a base de mercado nao esta importada.
    enrichment: dict | None = None
    # Captacao por gestora/casa sobre o mercado inteiro
    # (market.flows.summarize_watchlist_managers); None sem as bases em disco.
    # Calculada no build_report, que tem Config em maos - os renderers nao tem.
    flows: dict | None = None
    # CDI acumulado nas janelas de meses fechados (market.benchmarks.cdi_block);
    # None sem cdi.csv em disco - e nenhuma superficie inventa "% do CDI".
    benchmarks: dict | None = None

    def snapshot_for(self, platform: str) -> ShelfSnapshot | None:
        for snap in self.snapshots:
            if snap.platform == platform:
                return snap
        return None

    def diff_for(self, platform: str) -> ShelfDiff | None:
        for diff in self.shelf_diffs:
            if diff.platform == platform:
                return diff
        return None
