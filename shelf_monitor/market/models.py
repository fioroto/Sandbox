"""Modelos da base de mercado.

A hierarquia segue a RCVM 175, que separou o que antes era uma coisa so:

    fundo      envelope juridico, responsabilidade limitada por classe
      |
      +-- classe      unidade economica: CNPJ proprio, PL, politica, cota
            |
            +-- subclasse   segrega publico-alvo e condicoes comerciais,
                            compartilha carteira e CNPJ da classe

Por isso a classe - nao o fundo - e o grao central da base: e o CNPJ de classe
que distribuidores e o informe diario reportam.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class FeeUnit(str, Enum):
    """Unidade da taxa de administracao.

    O campo TAXA_ADM da CVM e polimorfico sem discriminador: para investidor
    qualificado o regulamento aceita texto livre, entao a mesma coluna carrega
    ora percentual ao ano, ora um valor fixo em reais. Sem essa distincao,
    qualquer ranking de custo produz outliers absurdos.
    """

    PERCENT = "percentual"
    CURRENCY = "reais"
    TEXT = "texto"
    UNKNOWN = "desconhecida"


class Origin(str, Enum):
    """De qual cadastro a linha veio."""

    RCVM175 = "rcvm175"
    LEGACY = "nao_adaptado"


@dataclass
class MarketFund:
    """Um fundo registrado - o envelope juridico."""

    registro_id: str
    cnpj: str | None
    name: str
    origin: Origin = Origin.RCVM175
    cvm_code: str | None = None
    fund_type: str | None = None
    situation: str | None = None
    situation_started_at: date | None = None
    registered_at: date | None = None
    constituted_at: date | None = None
    cancelled_at: date | None = None
    adapted_at: date | None = None
    net_worth: float | None = None
    net_worth_at: date | None = None
    director: str | None = None
    admin_name: str | None = None
    admin_cnpj: str | None = None
    manager_name: str | None = None
    manager_cnpj: str | None = None
    manager_person_type: str | None = None
    # No cadastro legado, classificacao e taxas vem no proprio registro do
    # fundo - nao ha classe onde guarda-las.
    legacy_fields: dict = field(default_factory=dict)

    @property
    def is_adapted(self) -> bool:
        """Fundo ja migrado para a RCVM 175 - suas classes e que importam."""
        return self.adapted_at is not None


@dataclass
class MarketClass:
    """Uma classe de cotas - o grao central da base."""

    classe_id: str
    fundo_id: str
    cnpj: str | None
    name: str
    cvm_code: str | None = None
    class_type: str | None = None
    situation: str | None = None
    situation_started_at: date | None = None
    registered_at: date | None = None
    constituted_at: date | None = None
    started_at: date | None = None
    # Classificacao CVM (Renda Fixa / Acoes / Multimercado / Cambial) e a da
    # ANBIMA, que e a granularidade com que o mercado realmente compara produto.
    cvm_classification: str | None = None
    anbima_classification: str | None = None
    benchmark: str | None = None
    share_class: str | None = None
    long_term_taxation: str | None = None
    investment_entity: str | None = None
    full_foreign_allowed: str | None = None
    esg: str | None = None
    condominium: str | None = None
    exclusive: str | None = None
    target_audience: str | None = None
    net_worth: float | None = None
    net_worth_at: date | None = None
    auditor_name: str | None = None
    auditor_cnpj: str | None = None
    custodian_name: str | None = None
    custodian_cnpj: str | None = None
    controller_name: str | None = None
    controller_cnpj: str | None = None


@dataclass
class MarketSubclass:
    """Uma subclasse - mesma carteira e mesmo CNPJ da classe, publico distinto."""

    subclasse_id: str
    classe_id: str
    name: str
    cvm_code: str | None = None
    situation: str | None = None
    situation_started_at: date | None = None
    constituted_at: date | None = None
    started_at: date | None = None
    condominium: str | None = None
    exclusive: str | None = None
    target_audience: str | None = None
    pension: str | None = None
    foreign_investor_only: str | None = None
    pension_plan_only: str | None = None


@dataclass
class ClassFees:
    """Taxas e condicoes comerciais, do extrato de informacoes."""

    cnpj: str
    reference_date: date | None = None
    admin_fee: float | None = None
    admin_fee_unit: FeeUnit = FeeUnit.UNKNOWN
    admin_fee_raw: str | None = None
    performance_fee: float | None = None
    performance_fee_benchmark: str | None = None
    has_performance_fee: str | None = None
    entry_fee: float | None = None
    exit_fee: float | None = None
    min_initial: float | None = None
    min_balance: float | None = None
    redemption_quotation_days: int | None = None
    redemption_payment_days: int | None = None
    target_audience: str | None = None
    investment_policy: str | None = None
    long_term_taxation: str | None = None


@dataclass
class MonthlyFlow:
    """Agregado mensal do informe diario para uma classe.

    O informe repete o CNPJ da classe em cada linha de subclasse; captacao e
    resgate somam trivialmente, e PL/cotistas somam apenas dentro do mesmo
    DT_COMPTC (as subclasses partilham a carteira, entao o PL da classe no dia
    e a soma das linhas daquele dia - somar dias diferentes inflaria tudo).

    A cota NAO soma nem se mistura: subclasses tem taxas distintas e portanto
    cotas distintas. A classe recebe a serie de UMA subclasse - a
    representativa, de maior PL no ultimo dia - e `quota_series` diz qual, para
    que o retorno entre meses so seja calculado sobre a mesma serie.
    """

    cnpj: str  # CNPJ da classe, 14 digitos
    month: str  # "YYYY-MM"
    inflow: float = 0.0  # soma de CAPTC_DIA no mes
    outflow: float = 0.0  # soma de RESG_DIA no mes
    net_worth: float | None = None  # VL_PATRIM_LIQ do ultimo DT_COMPTC
    shareholders: int | None = None  # NR_COTST do ultimo DT_COMPTC
    last_date: date | None = None  # maior DT_COMPTC vista no mes
    days: int = 0  # dias com informe - denuncia mes curto
    partial: bool = False  # mes corrente na data da importacao
    quota_end: float | None = None  # VL_QUOTA da serie representativa na last_date
    quota_series: str = ""  # ID_SUBCLASSE da serie ("" = classe sem subclasse)
    # Somas dos log-retornos diarios aceitos no mes (pares de dias informados
    # consecutivos da serie representativa) - a materia-prima da volatilidade,
    # acumulada em streaming para nao guardar a serie diaria.
    ret_sum: float = 0.0
    ret_sumsq: float = 0.0
    ret_n: int = 0

    @property
    def net(self) -> float:
        return self.inflow - self.outflow


@dataclass
class FlowsResult:
    """O resultado de uma importacao de captacao - espelha MarketDatabase."""

    imported_at: date
    window: list[str] = field(default_factory=list)  # meses pedidos, "YYYY-MM"
    flows: dict[tuple[str, str], MonthlyFlow] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)  # mes -> url/path
    # Zip do mes nao veio (o caso tipico: o mes corrente ainda nao publicado).
    missing_months: list[str] = field(default_factory=list)
    # Zip reusado do disco porque a origem estava fora - dado de ontem.
    stale_months: list[str] = field(default_factory=list)
    unknown_columns: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        classes = {cnpj for cnpj, _ in self.flows}
        return {
            "meses": len(self.window) - len(self.missing_months),
            "classes_com_captacao": len(classes),
            "linhas": len(self.flows),
        }


@dataclass
class MarketDatabase:
    """O resultado de uma importacao."""

    imported_at: date
    funds: list[MarketFund] = field(default_factory=list)
    classes: list[MarketClass] = field(default_factory=list)
    subclasses: list[MarketSubclass] = field(default_factory=list)
    fees: dict[str, ClassFees] = field(default_factory=dict)
    # Colunas vistas no arquivo que o modelo nao conhece, por tabela. E o sinal
    # de que a CVM mexeu no schema.
    unknown_columns: dict[str, list[str]] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    # Fontes cujo arquivo veio do disco porque a origem estava fora do ar - o
    # dado existe, mas nao e o de hoje.
    stale_sources: list[str] = field(default_factory=list)
    # Cadastro principal nao veio: gravar essa base sobrescreveria a anterior,
    # que e melhor.
    incomplete: bool = False
    notes: list[str] = field(default_factory=list)

    def classes_by_fund(self) -> dict[str, list[MarketClass]]:
        index: dict[str, list[MarketClass]] = {}
        for item in self.classes:
            index.setdefault(item.fundo_id, []).append(item)
        return index

    def subclasses_by_class(self) -> dict[str, list[MarketSubclass]]:
        index: dict[str, list[MarketSubclass]] = {}
        for item in self.subclasses:
            index.setdefault(item.classe_id, []).append(item)
        return index

    def counts(self) -> dict[str, int]:
        return {
            "fundos": len(self.funds),
            "fundos_adaptados": sum(1 for f in self.funds if f.is_adapted),
            "fundos_nao_adaptados": sum(
                1 for f in self.funds if f.origin == Origin.LEGACY
            ),
            "classes": len(self.classes),
            "subclasses": len(self.subclasses),
            "classes_com_taxa": len(self.fees),
        }
