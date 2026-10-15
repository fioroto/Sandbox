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
    # Ingresso e saida vem em tres colunas cada: o flag S/N, que a CVM preenche
    # em 100% das linhas, e os dois valores, preenchidos so quando o flag e S.
    # Guardar o flag separado importa porque ele e a unica leitura confiavel:
    # medido em 2026-09-20 sobre as 38.547 linhas do extrato, so 44 classes
    # (0,1%) cobram ingresso e 1.281 (3,3%) cobram saida. "Valor ausente" aqui
    # quer dizer "nao cobra", nao "nao informou" - e sem o flag as duas coisas
    # ficariam indistinguiveis.
    has_entry_fee: str | None = None
    entry_fee_pct: float | None = None
    entry_fee_amount: float | None = None
    has_exit_fee: str | None = None
    exit_fee_pct: float | None = None
    exit_fee_amount: float | None = None
    custody_fee_max: float | None = None
    min_initial: float | None = None
    redemption_quotation_days: int | None = None
    redemption_payment_days: int | None = None
    target_audience: str | None = None
    investment_policy: str | None = None
    long_term_taxation: str | None = None
    # Mandato declarado: o que o regulamento AUTORIZA, nunca o que a carteira
    # carrega. Um fundo pode ter autorizacao para 100% de credito privado e
    # carregar 10%. Medir exposicao realizada exigiria a CDA, que nao esta no
    # escopo - entao todo rotulo derivado daqui diz "autorizado".
    credit_private: str | None = None
    credit_private_max: float | None = None
    foreign: str | None = None
    foreign_max: float | None = None
    derivatives: str | None = None
    leverage: str | None = None
    leverage_factor: float | None = None
    mirror_fund: str | None = None


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
