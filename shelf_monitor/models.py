"""Modelos de dominio do monitor de prateleiras de fundos."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING

from shelf_monitor.texto import consertar_mojibake

if TYPE_CHECKING:  # enrich importa models - a volta so vale para o type checker
    from shelf_monitor.enrich import EnrichmentStats


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


# Aplicacao minima como a VITRINE a publicou, gravada pelo merge antes que a
# precedencia do Open Finance a substitua pelo minimo institucional da classe
# (ver `FundOffer.minimo_vitrine`).
KEY_MINIMO_VITRINE = "minimo_vitrine"

# A taxa como a VITRINE a publicou, gravada pelo merge pelo mesmo motivo (ver
# `FundOffer.taxa_da_vitrine`): a do Open Finance e outra conta.
KEY_TAXA_VITRINE = "taxa_vitrine"

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
    def minimo_vitrine(self) -> float | None:
        """A aplicacao minima que o cliente ve na vitrine - nunca a do catalogo.

        O merge da precedencia ao Open Finance, que informa o minimo
        institucional da classe, nao o ticket que o site cobra. Medido em
        2026-09-23: os 514 fundos da vitrine da XP que tambem estao no catalogo
        tinham `min_initial` do Open Finance, todos acima de R$ 5 mil, enquanto
        a propria lista da XP mostrava 80% da vitrine abaixo disso. Ler
        `min_initial` direto foi o que fez o relatorio concluir que a XP "nao
        tem porta de entrada de varejo".

        Duas vias, nesta ordem: o valor que o merge guardou da fonte de vitrine;
        ou, num fundo que nao passou pelo Open Finance, o proprio `min_initial`,
        que so pode ter vindo da vitrine. Sem nenhuma das duas, None - o monitor
        nao sabe o que a vitrine mostra.
        """
        valor = self.extra.get(KEY_MINIMO_VITRINE)
        if valor is not None:
            return valor
        if CollectorStrategy.OPEN_FINANCE.value not in self.sources:
            return self.min_initial
        return None

    @property
    def taxa_da_vitrine(self) -> float | None:
        """A taxa que o cliente ve no site da casa, em % ao ano: a taxa global
        (administracao, gestao e distribuicao), que o digest mostra nos gaps
        (pedido de 20/10/2026).

        O merge da precedencia ao Open Finance, cujo `maxAdminFee` e outra conta:
        vem em fracao (0,02 para 2%) e, em parte dos fundos, so com a taxa de
        administracao. Medido nas vitrines de 28/09 e 14/10/2026: o JGP Hedge
        com 0,02 no Open Finance e 2,0% no site do Itau; o Itau Small Cap
        Valuation com 0,0005 e 2,0%.

        Tres vias, nesta ordem: o valor que o merge guardou da fonte de vitrine;
        num snapshot gravado antes dessa chave, o valor da vitrine que o merge
        registrou como divergencia; num fundo que nao passou pelo Open Finance, o
        proprio `admin_fee_pct`. Sem nenhuma, None.
        """
        valor = self.extra.get(KEY_TAXA_VITRINE)
        if valor is not None:
            return valor
        divergencia = (self.extra.get("divergencias") or {}).get("admin_fee_pct") or {}
        if divergencia.get("fonte_alternativa") in LISTING_SOURCES:
            return divergencia.get("valor_alternativo")
        if CollectorStrategy.OPEN_FINANCE.value not in self.sources:
            return self.admin_fee_pct
        return None

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
        # Texto gravado com o charset errado e reparado na leitura (ver
        # shelf_monitor.texto): o relatorio nao publica "DÃ³lar", e a
        # comparacao com a coleta seguinte nao conta o conserto como mudanca.
        return cls(
            platform=data["platform"],
            name=consertar_mojibake(data["name"]),
            cnpj=data.get("cnpj"),
            isin=data.get("isin"),
            # Snapshot anterior ao lookup por codigo nao tem a chave: le como
            # None e o historico continua comparavel.
            cvm_code=data.get("cvm_code"),
            manager_name=consertar_mojibake(data.get("manager_name")),
            manager_cnpj=data.get("manager_cnpj"),
            admin_name=consertar_mojibake(data.get("admin_name")),
            asset_class=AssetClass(data.get("asset_class", "outros")),
            raw_category=consertar_mojibake(data.get("raw_category")),
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
    # So no relatorio, nunca gravado: a coleta do dia nao trouxe vitrine
    # utilizavel e as analises usam esta, a ultima valida. O texto diz de que
    # dia e por que (`shelf_monitor.reserva`).
    reserva: str | None = None

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

    @property
    def is_cancelled(self) -> bool:
        """Cancelado na CVM - fica fora do radar dos relatorios (14/10/2026)."""
        from shelf_monitor.normalize import strip_accents

        return "CANCELAD" in strip_accents(self.situation or "").upper()

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
    # A vitrine apareceu ou cresceu alem de SUSPECT_GROWTH_RATIO: a coleta passou
    # a ver mais, e o que ela ve pela primeira vez nao e movimento de mercado.
    nova_linha_de_base: bool = False

    @property
    def is_first_run(self) -> bool:
        """Sem comparacao possivel: primeira coleta, ou vitrine que mudou de escala.

        A nova linha de base entra aqui para que toda superficie que ja trata a
        primeira coleta como base - digest, deck, resumo, assunto do e-mail -
        trate igual o ganho de cobertura, sem regra propria em cada uma.
        """
        return self.prev_date is None or self.nova_linha_de_base

    def to_dict(self) -> dict:
        return {
            "platform": self.platform,
            "prev_date": self.prev_date.isoformat() if self.prev_date else None,
            "curr_date": self.curr_date.isoformat(),
            "suspect": self.suspect,
            "suspect_reason": self.suspect_reason,
            "nova_linha_de_base": self.nova_linha_de_base,
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

    @property
    def is_cancellation(self) -> bool:
        """O fundo passou a cancelado."""
        from shelf_monitor.normalize import strip_accents

        return "CANCELAD" in strip_accents(self.new_situation or "").upper()

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


# Blocos que um modulo pode suprimir (ver RunReport.suprimidos).
# Tudo que conta cotistas: ponderacao, "onde esta a gente", "+N cotistas".
BLOCO_COTISTAS = "cotistas"
# Tudo que cruza vitrines por CNPJ: gaps, exclusivos, consenso entre casas.
BLOCO_CNPJ = "cnpj"
# O corte produto da gestora da propria casa x terceiros, pelo gestor registrado.
BLOCO_PRODUTO_PROPRIO = "produto_proprio"


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
    # O mesmo dado como objeto. Existe ao lado do dict porque as duas formas tem
    # consumidores diferentes: dashboard e aggregate serializam o dict para JSON,
    # e a planilha le campos do objeto. Derivar um do outro no renderer seria
    # reconstruir o que build_report ja tem na mao.
    enrichment_stats: EnrichmentStats | None = None
    # Captacao dos fundos de vitrine (report.retail_flow.build_retail_flow) e as
    # oportunidades de grade que ela abre. None quando o informe diario da CVM
    # nao estava disponivel - as superficies omitem o bloco em vez de inventar.
    retail_flow: dict | None = None
    grade_opportunities: dict | None = None
    # Rentabilidade da vitrine ponderada por PL e por cotistas
    # (report.performance.build_performance). None quando o informe diario nao
    # estava disponivel - as superficies omitem o bloco em vez de inventar.
    performance: dict | None = None
    # A mesma leitura em 12 meses (report.performance.build_performance_mensal),
    # sobre a serie mensal do informe - base no fechamento da competencia
    # anterior, a convencao de lamina. Cada bloco traz em `janela` o rotulo, as
    # datas, os pregoes e a base, que toda superficie escreve junto do numero.
    performance_12m: dict | None = None
    # Custo relativo a categoria (report.custo) e mandato declarado
    # (report.mandato). Ambos saem do extrato da CVM, entao dependem da base de
    # mercado importada - None quando ela falta.
    custo: dict | None = None
    mandato: dict | None = None
    # Rentabilidade por fundo em janelas (1m, 3m, YTD, 12m, 24m), de
    # report.rentabilidade. Custa a leitura de ate 24 competencias do informe
    # (~2,5 min e ~260 MB na primeira vez), entao sai atras de
    # `--with-rentabilidade` em `run`, `report` e `export`.
    # O docstring dizia isso desde sempre; o codigo so passou a cumprir agora.
    rentabilidade: dict | None = None
    # Blocos que o modulo declarou sem sentido no dominio dele
    # (`Config.blocos_suprimidos`): chave -> motivo. Cada superficie troca o
    # bloco pelo motivo, em vez de publicar numero que nao mede nada. Vazio no
    # monitor de fundos; a previdencia suprime BLOCO_COTISTAS e BLOCO_CNPJ.
    suprimidos: dict[str, str] = field(default_factory=dict)
    # Insumos do bloco de gaps: o mapa de familias de estrategia
    # (familias.MapaDeFamilias, montado da carteira mensal da CVM) e a lista
    # distribuivel (distribuivel.BaseDistribuivel, a pasta de CSVs). Objetos e
    # nao dicts porque o bloco consulta o mapa gap a gap. None quando a fonte
    # falta - o bloco volta a comparar CNPJ contra CNPJ e diz por que.
    familias: object | None = None
    distribuivel: object | None = None
    # A captacao na XP dos fundos de vitrine, pelas bases internas
    # (report.captacao_na_xp), nas janelas do bloco de captacao. None sem o
    # informe diario ou onde o cruzamento por CNPJ nao se aplica.
    captacao_xp: dict | None = None

    def suprime(self, bloco: str) -> str | None:
        """O motivo, quando o bloco esta suprimido; None quando ele vale."""
        return self.suprimidos.get(bloco)

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
