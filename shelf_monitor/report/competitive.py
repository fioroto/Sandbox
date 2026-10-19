"""Leitura competitiva das vitrines: o que cada casa esta fazendo, e o que a XP faz com isso.

O `aggregate` responde *quanto* cada casa tem. Este modulo responde *o que cada
uma escolheu ser* e *o que a XP deveria decidir* - e a diferenca entre um painel
e uma recomendacao.

Tres regras que valem para tudo aqui:

**A base e a vitrine, nunca o catalogo.** Posicionamento e o que o cliente ve na
tela. O catalogo Open Finance mede o que a casa administra, o que e outra
pergunta - somar os 10.073 do BTG contra os 668 da XP compararia coisas
diferentes e daria um numero espetacularmente errado. Reusamos `listing_funds`
do aggregate para que a regra de pertinencia (varejo + vitrine) exista num unico
lugar.

**Cada metrica declara quem pode entrar nela.** Medido em 2026-09-19: o Inter
publica nome e link, sem gestora, minimo ou taxa (0% de cobertura nos tres); o Nu
nao tem vitrine deslogada nenhuma (so catalogo). Uma media de taxa "do mercado"
que silenciosamente exclui duas das cinco casas nao e uma media do mercado. Por
isso todo bloco carrega `participantes` e `fora`, com motivo.

**Indice de concentracao, nao share bruto.** "18% da vitrine da casa X e renda
fixa" nao diz nada sozinho; "X tem 1,8x mais renda fixa que a media das
concorrentes" diz onde a casa apostou. O indice e share da casa dividido pelo
share das *demais* (nao do total, que incluiria a propria casa e puxaria todo
indice para 1).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shelf_monitor.classify import (
    CATEGORIA_ESTRUTURADO,
    KEY_VIA,
    label_nivel1,
    so_nivel1,
)
from shelf_monitor.config import Config
from shelf_monitor.enrich import assinatura_da_gestora
from shelf_monitor.models import BLOCO_CNPJ, FundOffer, ShelfSnapshot, SnapshotStatus
from shelf_monitor.report.aggregate import (
    _canonical_order,
    listing_funds,
    _median,
    _offer_manager,
    _platform_label,
    anbima_category,
    categoria_comparavel,
    gestoras_da_casa,
    is_own_product,
)
from shelf_monitor.storage import load_shelf_snapshot, shelf_snapshot_dates
from shelf_monitor.texto import contagem
from shelf_monitor.report.shared import (
    MIN_AMOSTRA_TICKET,
    MIN_VITRINE,
    SOBRE_INDICE,
    SUB_INDICE,
)

# Abaixo disto a vitrine nao sustenta leitura de posicionamento: um punhado de
# fundos destacados na home nao e a prateleira da casa.
_MIN_VITRINE = MIN_VITRINE

# Fracao minima de um campo preenchido para a metrica daquele campo valer. Com
# 30% de cobertura a mediana descreve o terco que foi publicado, nao a vitrine.
_MIN_FIELD_COVERAGE = 0.5

# Indice fora desta faixa e aposta declarada, nao ruido de arredondamento.
_OVER_INDEX = SOBRE_INDICE
_UNDER_INDEX = SUB_INDICE

# Uma categoria com meia duzia de fundos gera indice instavel (um fundo a mais
# dobra o share). Fora do ranking de apostas, mas continua contada no total.
_MIN_CATEGORY_FUNDS = 5

# Teto de aplicacao inicial que ainda e varejo de verdade.
_RETAIL_TICKET = 1_000.0

# Cobertura de CNPJ minima para a casa entrar em qualquer conta que dependa de
# identidade (exclusivos, consenso, gaps). Medido em 2026-09-19: a vitrine do
# Itau traz CNPJ em 27 de 458 fundos (6%) - a lista de rentabilidade dele nao
# publica o numero. Sem a guarda, ele aparecia com "4 fundos exclusivos (1%)",
# que nao e uma leitura de prateleira: e a fracao dele que o monitor consegue
# identificar. O merge reconcilia pelo slug do nome contra o Open Finance, mas na
# vitrine so o CNPJ serve para cruzar casas.
_MIN_CNPJ_COVERAGE = 0.5

# Minimo de fundos com ticket lido da vitrine para a mediana significar algo.
# Abaixo disso a casa entra como "sem base na coleta", nao como "publica caro".
# O mesmo limiar vale para a leitura de ticket das superficies (`aggregate`).
_MIN_TICKET_SAMPLE = MIN_AMOSTRA_TICKET


def _coverage(funds: list[FundOffer], attr: str) -> float:
    if not funds:
        return 0.0
    filled = sum(1 for f in funds if getattr(f, attr, None) is not None)
    return filled / len(funds)


def _has_field(funds: list[FundOffer], attr: str) -> bool:
    return _coverage(funds, attr) >= _MIN_FIELD_COVERAGE


# Os dois eixos de categoria, e por que agora sao o MESMO eixo em duas
# granularidades.
#
# Ate 2026-09-20 eram duas taxonomias: o eixo grosso vinha da heuristica
# `asset_class` e o fino da ANBIMA. A mistura produzia numero espetacularmente
# errado - sem casar com o cadastro, um fundo do Itau caia no rotulo grosso
# "Renda Fixa", que as outras casas quase nao usavam, e o Itau aparecia com 17x
# o share das concorrentes ali e 11x em "Multimercado". Nao era aposta de
# prateleira: era o balde que so ele usava, porque so ele nao casava.
#
# A causa nunca foi a taxonomia, e sim a identidade: o Itau publica CNPJ em 5,9%
# da vitrine, e nenhuma casa publica codigo CVM. ANBIMA e CVM tinham exatamente
# a mesma cobertura em toda casa - onde o join acontece as duas chegam juntas,
# onde nao acontece nenhuma chega.
#
# Com `shelf_monitor.classify` resolvendo a categoria pelo nome quando falta
# identidade, a cobertura do Itau foi a 100% e os dois eixos passaram a sair da
# mesma fonte: o grosso e o nivel 1 da categoria oficial, o fino e ela inteira.
# Tres guardas mantem a comparacao honesta, e cada uma corrige um artefato
# medido: rotulo publicado pela casa nao vale como oficial (o BTG aparecia com
# "Direitos Creditorios" que so ele tem); fundo resolvido so no nivel 1 nao
# entra no eixo fino (o Itau aparecia com "RENDA FIXA 22,20x"); e produto
# estruturado tem balde proprio (a XP aparecia apostando "16,6x mais
# Estruturado", que e tipo de produto, nao composicao de prateleira).
#
# O limiar abaixo deixou de medir cobertura ANBIMA e passou a medir cobertura de
# CLASSIFICACAO, por qualquer das vias. Com a cascata, as quatro vitrines ficam
# entre 94% e 100%.
_MIN_CLASSIFICACAO = 0.5

# Rotulo unico para o que nao tem classe conhecida. Fica fora do ranking de
# apostas: "a casa X tem 2,8x mais Sem classificacao" e um fato sobre o join com a
# base oficial, nao sobre a estrategia da casa.
_UNCLASSIFIED = "Sem classificação"

# Produto que o cadastro da CVM nao classifica (FIDC, FII, FIP...). Fica de
# fora do ranking de apostas como o "Sem classificacao", mas por outro motivo:
# ali falta join, aqui a taxonomia simplesmente nao cobre o produto.
#
# Deixa-lo dentro produzia numero enganoso: medido em 2026-09-20, a XP aparecia
# apostando "16,6x mais Estruturado" que as concorrentes. Nao e composicao de
# prateleira - e que 6,7% da vitrine dela sao FIDC/FII, contra 0,6% do BTG, e o
# balde existe justamente porque a ANBIMA nao classifica esses produtos. A
# diferenca e real e merece bloco proprio, nao uma linha no ranking de apostas.
_ESTRUTURADO = "Estruturado"

# Rotulos que contam no total mas nao disputam indice de concentracao.
_FORA_DO_EIXO = frozenset({_UNCLASSIFIED, _ESTRUTURADO})


# Rotulo do eixo grosso gravado por quem precisa de um nivel 1 diferente do que
# `label_nivel1` deriva da categoria. Hoje so a previdencia: todo rotulo ANBIMA
# dela tem nivel 1 "Previdencia", e o eixo grosso de uma vitrine so de
# previdencia colapsava num balde unico (shelf_monitor.previdencia.eixo). O
# modulo de fundos nunca grava esta chave - para ele, nada muda.
KEY_CATEGORIA_GROSSA = "categoria_grossa"


def classe_grossa(categoria: str | None, grossa: str | None = None) -> str:
    """Nivel 1 de uma categoria oficial - o eixo grosso, comparavel entre casas.

    `grossa` e o rotulo gravado em `KEY_CATEGORIA_GROSSA`, quando o fundo tem.
    O custo usa direto (a categoria dele pode vir do extrato, nao da oferta).
    """
    if not categoria:
        return _UNCLASSIFIED
    if categoria == CATEGORIA_ESTRUTURADO:
        return _ESTRUTURADO
    return grossa or label_nivel1(categoria)


def coarse_category(fund: FundOffer) -> str:
    """Nivel 1 da categoria oficial - o eixo grosso, comparavel entre casas."""
    return classe_grossa(categoria_comparavel(fund), fund.extra.get(KEY_CATEGORIA_GROSSA))


def fine_category(fund: FundOffer) -> str:
    """A categoria oficial inteira - o eixo fino, mesma taxonomia do grosso.

    Fundo resolvido so no nivel 1 fica de fora: ele tem categoria para o eixo
    grosso e nao para o fino, e promove-lo aqui criaria um balde grosso dentro
    da comparacao fina - o artefato que o eixo unico veio corrigir.
    """
    categoria = categoria_comparavel(fund)
    if not categoria or categoria == CATEGORIA_ESTRUTURADO:
        return _UNCLASSIFIED
    if so_nivel1(fund.extra.get(KEY_VIA)):
        return _UNCLASSIFIED
    return categoria


def classificacao_coverage(funds: list[FundOffer]) -> float:
    """Fracao da vitrine com categoria do cadastro, por qualquer via.

    Mede classificacao, nao ANBIMA: o resolver por nome devolve categoria
    ANBIMA tanto quanto o join por CNPJ devolve. O que muda e a procedencia,
    que fica em `categoria_via`.
    """
    if not funds:
        return 0.0
    return sum(1 for f in funds if categoria_comparavel(f)) / len(funds)


def _counts(funds: list[FundOffer], categoria) -> dict[str, int]:
    counts: dict[str, int] = {}
    for fund in funds:
        label = categoria(fund)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _shares(funds: list[FundOffer], categoria) -> dict[str, float]:
    """Share por categoria, calculado sobre os fundos CLASSIFICADOS.

    O denominador exclui os sem classe: com ele dentro, uma casa com join pior
    teria todos os shares diluidos e pareceria sub-indexada em tudo.
    """
    counts = _counts(funds, categoria)
    classificados = sum(n for cat, n in counts.items() if cat not in _FORA_DO_EIXO)
    if not classificados:
        return {}
    return {
        cat: n / classificados for cat, n in counts.items() if cat not in _FORA_DO_EIXO
    }


# --- 1. posicionamento --------------------------------------------------------


@dataclass
class CategoryBet:
    """Uma categoria em que a casa esta acima ou abaixo das concorrentes."""

    category: str
    funds: int
    share: float
    peer_share: float
    index: float

    def to_dict(self) -> dict:
        return {
            "category": self.category,
            "funds": self.funds,
            "share": round(self.share, 4),
            "peer_share": round(self.peer_share, 4),
            "index": round(self.index, 2),
        }


@dataclass
class PlatformPosition:
    """Como uma casa escolheu montar a vitrine dela."""

    platform_id: str
    label: str
    vitrine: int
    categories: int
    over_index: list[CategoryBet] = field(default_factory=list)
    under_index: list[CategoryBet] = field(default_factory=list)
    # Eixo fino - vazio para quem nao tem cobertura (ver _MIN_CLASSIFICACAO).
    fine_over: list[CategoryBet] = field(default_factory=list)
    fine_under: list[CategoryBet] = field(default_factory=list)
    classificacao_cov: float = 0.0
    median_min: float | None = None
    retail_ticket_share: float | None = None
    # Mediana da taxa da vitrine INTEIRA - mede composicao, nao preco: uma casa
    # com mais renda fixa (~0,2% ao ano) sai "mais barata" que uma com mais
    # acoes (~2%) sem que produto nenhum custe diferente. O nome diz isso para
    # que ninguem a leia como custo. O custo comparavel esta em `report.custo`,
    # que divide cada fundo pela mediana da propria categoria.
    median_admin_fee_mix: float | None = None
    own_product_share: float | None = None
    distinct_managers: int | None = None
    top3_manager_share: float | None = None
    # None quando a vitrine nao tem CNPJ suficiente para cruzar com as outras.
    exclusive: int | None = 0
    exclusive_share: float | None = 0.0
    sem_dado: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "vitrine": self.vitrine,
            "categories": self.categories,
            "over_index": [b.to_dict() for b in self.over_index],
            "under_index": [b.to_dict() for b in self.under_index],
            "fine_over": [b.to_dict() for b in self.fine_over],
            "fine_under": [b.to_dict() for b in self.fine_under],
            "classificacao_cov": round(self.classificacao_cov, 3),
            "median_min": self.median_min,
            "retail_ticket_share": self.retail_ticket_share,
            "median_admin_fee_mix": self.median_admin_fee_mix,
            "own_product_share": self.own_product_share,
            "distinct_managers": self.distinct_managers,
            "top3_manager_share": self.top3_manager_share,
            "exclusive": self.exclusive,
            "exclusive_share": (
                None if self.exclusive_share is None else round(self.exclusive_share, 4)
            ),
            "sem_dado": self.sem_dado,
        }


def vitrine_snapshots(snapshots: list[ShelfSnapshot]) -> tuple[dict[str, list[FundOffer]], list[dict]]:
    """Vitrines utilizaveis, e quem ficou fora com o motivo.

    Uma casa sem vitrine nao e uma casa com vitrine vazia: excluir com motivo e
    o que impede que "o Nu nao tem renda fixa na prateleira" seja publicado
    quando o que houve e que o Nu nao tem prateleira deslogada.
    """
    usable: dict[str, list[FundOffer]] = {}
    excluded: list[dict] = []

    for snapshot in snapshots:
        label = _platform_label(snapshot.platform)
        if snapshot.status == SnapshotStatus.UNAVAILABLE or not snapshot.funds:
            excluded.append({
                "platform_id": snapshot.platform, "label": label,
                "reason": "coleta indisponível nesta execução",
            })
            continue
        listing = listing_funds(snapshot)
        if not listing:
            excluded.append({
                "platform_id": snapshot.platform, "label": label,
                "reason": (
                    f"sem vitrine coletada (estratégia: {snapshot.strategy.value}); "
                    "o catálogo Open Finance não é vitrine"
                ),
            })
            continue
        if len(listing) < _MIN_VITRINE:
            excluded.append({
                "platform_id": snapshot.platform, "label": label,
                "reason": f"vitrine com {len(listing)} fundos, abaixo do mínimo de {_MIN_VITRINE}",
            })
            continue
        usable[snapshot.platform] = listing

    return usable, excluded


def _exclusive_on_vitrine(vitrines: dict[str, list[FundOffer]]) -> dict[str, set[str]]:
    """CNPJ presente na vitrine de exatamente uma casa."""
    presence: dict[str, set[str]] = {}
    for platform_id, funds in vitrines.items():
        for fund in funds:
            if fund.cnpj:
                presence.setdefault(fund.cnpj, set()).add(platform_id)
    exclusive: dict[str, set[str]] = {pid: set() for pid in vitrines}
    for cnpj, platforms in presence.items():
        if len(platforms) == 1:
            exclusive[next(iter(platforms))].add(cnpj)
    return exclusive


def cnpj_coverage(funds: list[FundOffer]) -> float:
    if not funds:
        return 0.0
    return sum(1 for f in funds if f.cnpj) / len(funds)


def identifiable_vitrines(
    vitrines: dict[str, list[FundOffer]]
) -> tuple[dict[str, list[FundOffer]], list[dict]]:
    """Vitrines em que da para cruzar fundos entre casas (ver _MIN_CNPJ_COVERAGE)."""
    dentro = {
        pid: funds for pid, funds in vitrines.items()
        if cnpj_coverage(funds) >= _MIN_CNPJ_COVERAGE
    }
    fora = [
        {
            "platform_id": pid,
            "label": _platform_label(pid),
            "reason": (
                f"CNPJ em apenas {cnpj_coverage(funds):.0%} da vitrine - não dá para "
                "cruzar os fundos dela com as outras casas"
            ),
        }
        for pid, funds in vitrines.items() if pid not in dentro
    ]
    return dentro, fora


def vitrine_only_values(funds: list[FundOffer], attr: str) -> list[float]:
    """Valores de um campo que so podem ter vindo da VITRINE, nao do catalogo.

    Existe porque o merge une fontes e o Open Finance vence na precedencia. Um
    fundo cujas fontes nao incluem o Open Finance necessariamente teve o campo
    preenchido pela vitrine - e esse subconjunto e o unico comparavel entre casas.
    Para aplicacao minima ha uma via a mais, `FundOffer.minimo_vitrine`: o valor
    que o merge guardou da fonte de vitrine antes da precedencia agir.

    Historico que explica a regra: em 2026-09-19, dos 514 fundos da vitrine da XP
    com aplicacao minima, todos tinham o valor vindo do Open Finance, e os 154 so
    de navegador nao tinham nenhum. Lia-se "a vitrine da XP nao publica minimo".
    Nao era a vitrine: era o coletor, que nao reconhecia o campo
    `minimalInitialInvestment` do XHR da lista. Conferido em 2026-09-23, a lista
    da XP exibe o minimo e 78% dela tem minimo de ate R$ 1.000. Comparar direto o
    minimo institucional da classe com o ticket que o site mostra concluiria que
    a XP pede R$ 50 mil onde as concorrentes pedem R$ 1 mil.
    """
    if attr == "min_initial":
        return [value for f in funds if (value := f.minimo_vitrine) is not None]
    return [
        value for f in funds
        if "open-finance" not in f.sources
        and (value := getattr(f, attr, None)) is not None
    ]


def _bets(
    funds: list[FundOffer], peers: list[FundOffer], categoria
) -> tuple[list[CategoryBet], list[CategoryBet]]:
    """Categorias em que a casa esta acima e abaixo dos pares, num eixo."""
    own_shares = _shares(funds, categoria)
    peer_shares = _shares(peers, categoria)
    own_counts = _counts(funds, categoria)

    bets: list[CategoryBet] = []
    for category, share in own_shares.items():
        if own_counts[category] < _MIN_CATEGORY_FUNDS:
            continue
        peer_share = peer_shares.get(category, 0.0)
        # Categoria que nenhum par tem: aposta exclusiva, e o indice seria divisao
        # por zero. Infinito deixa o renderer dizer "so esta casa tem".
        index = share / peer_share if peer_share else float("inf")
        bets.append(CategoryBet(category, own_counts[category], share, peer_share, index))

    over = sorted([b for b in bets if b.index >= _OVER_INDEX], key=lambda b: -b.index)
    under = [b for b in bets if b.index <= _UNDER_INDEX and b.peer_share > 0]
    # Categoria ausente na casa e presente em volume nos pares e a sub-indexacao
    # mais forte que existe - indice zero.
    for category, peer_share in peer_shares.items():
        if category not in own_shares and peer_share >= 0.02:
            under.append(CategoryBet(category, 0, 0.0, peer_share, 0.0))
    under.sort(key=lambda b: (b.index, -b.peer_share))
    return over, under


def build_positioning(snapshots: list[ShelfSnapshot]) -> dict:
    """Assinatura de cada vitrine: onde a casa apostou acima e abaixo das demais."""
    vitrines, excluded = vitrine_snapshots(snapshots)
    if len(vitrines) < 2:
        return {
            "available": False,
            "reason": (
                f"{contagem(len(vitrines), 'vitrine utilizável', 'vitrines utilizáveis')}: "
                "posicionamento relativo precisa de ao menos duas para haver com o que "
                "comparar"
            ),
            "excluded": excluded,
        }

    identificaveis, sem_identidade = identifiable_vitrines(vitrines)
    exclusive = _exclusive_on_vitrine(identificaveis)
    positions: list[PlatformPosition] = []

    for platform_id in _canonical_order(set(vitrines)):
        funds = vitrines[platform_id]
        peers = [f for pid, fs in vitrines.items() if pid != platform_id for f in fs]

        over, under = _bets(funds, peers, coarse_category)

        # Eixo fino: so entra quem tem cobertura de classificacao, e a
        # comparacao e contra os pares que TAMBEM tem - comparar categoria fina
        # contra quem nao a tem mediria a cobertura do join, nao a prateleira.
        cobertura = classificacao_coverage(funds)
        fine_peers = [
            f for pid, fs in vitrines.items()
            if pid != platform_id and classificacao_coverage(fs) >= _MIN_CLASSIFICACAO
            for f in fs
        ]
        if cobertura >= _MIN_CLASSIFICACAO and fine_peers:
            fine_over, fine_under = _bets(funds, fine_peers, fine_category)
        else:
            fine_over, fine_under = [], []

        sem_dado: list[str] = []
        # So o minimo que a VITRINE mostra: o `min_initial` de um fundo que
        # tambem esta no catalogo e o minimo institucional do Open Finance.
        mins = vitrine_only_values(funds, "min_initial")
        if funds and len(mins) / len(funds) >= _MIN_FIELD_COVERAGE:
            median_min = _median(mins)
            retail_share = sum(1 for v in mins if v <= _RETAIL_TICKET) / len(mins)
        else:
            median_min = retail_share = None
            sem_dado.append("aplicação mínima")

        if _has_field(funds, "admin_fee_pct"):
            median_fee = _median([f.admin_fee_pct for f in funds if f.admin_fee_pct is not None])
        else:
            median_fee = None
            sem_dado.append("taxa de administração")

        managers = [m for m in (_offer_manager(f) for f in funds) if m]
        if len(managers) / len(funds) >= _MIN_FIELD_COVERAGE:
            per_manager: dict[str, int] = {}
            for manager in managers:
                per_manager[manager] = per_manager.get(manager, 0) + 1
            top3 = sorted(per_manager.values(), reverse=True)[:3]
            distinct = len(per_manager)
            top3_share = sum(top3) / len(managers)
            own = [is_own_product(platform_id, f) for f in funds]
            known = [v for v in own if v is not None]
            own_share = (sum(1 for v in known if v) / len(known)) if known else None
        else:
            distinct = top3_share = own_share = None
            sem_dado.append("gestora")

        positions.append(PlatformPosition(
            platform_id=platform_id,
            label=_platform_label(platform_id),
            vitrine=len(funds),
            categories=len(_shares(funds, coarse_category)),
            over_index=over[:4],
            under_index=under[:4],
            fine_over=fine_over[:4],
            fine_under=fine_under[:4],
            classificacao_cov=cobertura,
            median_min=median_min,
            retail_ticket_share=retail_share,
            median_admin_fee_mix=median_fee,
            own_product_share=own_share,
            distinct_managers=distinct,
            top3_manager_share=top3_share,
            exclusive=(
                len(exclusive[platform_id]) if platform_id in identificaveis else None
            ),
            exclusive_share=(
                len(exclusive[platform_id]) / len(funds)
                if platform_id in identificaveis and funds
                else None
            ),
            sem_dado=sem_dado + (
                [] if platform_id in identificaveis else ["identidade por CNPJ"]
            ),
        ))

    return {
        "available": True,
        "participantes": [p.platform_id for p in positions],
        "excluded": excluded,
        "sem_identidade": sem_identidade,
        "positions": [p.to_dict() for p in positions],
        "_positions": positions,
    }


# --- 2. gestoras --------------------------------------------------------------


@dataclass
class ManagerReach:
    """Alcance de uma gestora nas vitrines coletadas."""

    manager: str
    platforms: list[str]
    funds_por_casa: dict[str, int]

    @property
    def reach(self) -> int:
        return len(self.platforms)

    @property
    def total_funds(self) -> int:
        return sum(self.funds_por_casa.values())

    def to_dict(self) -> dict:
        return {
            "manager": self.manager,
            "platforms": self.platforms,
            "reach": self.reach,
            "funds_por_casa": self.funds_por_casa,
            "total_funds": self.total_funds,
        }


def build_manager_reach(snapshots: list[ShelfSnapshot], baseline_id: str = "xp") -> dict:
    """Quem esta ganhando distribuicao, e onde a XP nao esta na foto.

    Gestora e o eixo em que uma decisao comercial de verdade acontece: adicionar
    um fundo de gestora que a casa ja distribui e operacional; abrir uma gestora
    nova e negociacao. O bloco separa exatamente isso.
    """
    vitrines, excluded = vitrine_snapshots(snapshots)
    com_gestora = {
        pid: funds for pid, funds in vitrines.items()
        if len([m for m in (_offer_manager(f) for f in funds) if m]) / len(funds)
        >= _MIN_FIELD_COVERAGE
    }
    sem_gestora = [
        {"platform_id": pid, "label": _platform_label(pid), "reason": "vitrine sem gestora publicada"}
        for pid in vitrines if pid not in com_gestora
    ]
    if len(com_gestora) < 2:
        return {
            "available": False,
            "reason": (
                f"{contagem(len(com_gestora), 'vitrine', 'vitrines')} com gestora "
                "publicada: o alcance por gestora precisa de ao menos duas"
            ),
            "excluded": excluded + sem_gestora,
        }

    por_gestora: dict[str, dict[str, int]] = {}
    for platform_id, funds in com_gestora.items():
        for fund in funds:
            manager = _offer_manager(fund)
            if not manager:
                continue
            por_gestora.setdefault(manager, {})
            por_gestora[manager][platform_id] = por_gestora[manager].get(platform_id, 0) + 1

    reaches = [
        ManagerReach(manager, _canonical_order(set(casas)), casas)
        for manager, casas in por_gestora.items()
    ]
    reaches.sort(key=lambda r: (-r.reach, -r.total_funds, r.manager))

    concorrentes = [pid for pid in com_gestora if pid != baseline_id]
    ausentes_na_xp = [
        r for r in reaches
        if baseline_id in com_gestora
        and baseline_id not in r.funds_por_casa
        and len([p for p in r.platforms if p in concorrentes]) >= 2
    ]
    exclusivas: dict[str, list[ManagerReach]] = {pid: [] for pid in com_gestora}
    for reach in reaches:
        if reach.reach == 1:
            exclusivas[reach.platforms[0]].append(reach)

    return {
        "available": True,
        "participantes": list(com_gestora),
        "excluded": excluded + sem_gestora,
        "baseline_id": baseline_id,
        "total_gestoras": len(reaches),
        "total_ausentes_no_baseline": len(ausentes_na_xp),
        "mais_distribuidas": [r.to_dict() for r in reaches[:12]],
        "ausentes_no_baseline": [r.to_dict() for r in ausentes_na_xp[:12]],
        "exclusivas": {
            pid: [r.to_dict() for r in sorted(rs, key=lambda r: -r.total_funds)[:6]]
            for pid, rs in exclusivas.items()
        },
        "_reaches": reaches,
        "_ausentes": ausentes_na_xp,
    }


# --- 3. momentum --------------------------------------------------------------


def build_momentum(config: Config, snapshots: list[ShelfSnapshot]) -> dict:
    """O que cada casa mexeu na vitrine desde o snapshot anterior.

    Guarda de comparabilidade, e a razao de ela existir: a vitrine da XP, do BTG
    e do Itau so passou a ser coletada em 2026-09-19 (antes a CDN devolvia 403 ao
    navegador headless). Comparar a vitrine de hoje com a de ontem, que era
    vazia, daria "a XP lancou 668 fundos" - e a coleta que nasceu, nao a
    prateleira. Enquanto os dois lados nao tiverem vitrine, o bloco diz que a
    serie comeca agora, em vez de publicar o artefato.
    """
    vitrines, excluded = vitrine_snapshots(snapshots)
    movimentos: list[dict] = []
    sem_base: list[dict] = []

    for platform_id, funds_hoje in vitrines.items():
        label = _platform_label(platform_id)
        atual = next(s for s in snapshots if s.platform == platform_id)
        anteriores = [d for d in shelf_snapshot_dates(config, platform_id) if d < atual.snapshot_date]
        if not anteriores:
            sem_base.append({
                "platform_id": platform_id, "label": label,
                "reason": "primeiro snapshot desta casa",
            })
            continue

        caminho = config.platform_snapshots_dir(platform_id) / f"{max(anteriores).isoformat()}.json"
        anterior = load_shelf_snapshot(caminho)
        vitrine_anterior = listing_funds(anterior)
        if not vitrine_anterior:
            sem_base.append({
                "platform_id": platform_id, "label": label,
                "reason": (
                    f"snapshot de {max(anteriores).isoformat()} não tinha vitrine "
                    f"(estratégia: {anterior.strategy.value}); a série de vitrine "
                    f"começa em {atual.snapshot_date.isoformat()}"
                ),
            })
            continue

        antes = {f.identity: f for f in vitrine_anterior}
        agora = {f.identity: f for f in funds_hoje}
        # `sorted` porque a diferenca de `dict.keys()` e um set, cuja ordem de
        # iteracao muda entre processos (hash de str e randomizado). Sem isto,
        # `exemplos_entrada` traz cinco fundos diferentes a cada execucao e o
        # empate em `saldo_por_categoria` muda quais categorias sobrevivem ao
        # corte - duas execucoes seguidas do mesmo codigo, sobre os mesmos
        # snapshots, davam JSON diferente.
        entradas = [agora[k] for k in sorted(agora.keys() - antes.keys())]
        saidas = [antes[k] for k in sorted(antes.keys() - agora.keys())]

        por_categoria: dict[str, int] = {}
        for fund in entradas:
            categoria = anbima_category(fund)
            por_categoria[categoria] = por_categoria.get(categoria, 0) + 1
        for fund in saidas:
            categoria = anbima_category(fund)
            por_categoria[categoria] = por_categoria.get(categoria, 0) - 1

        movimentos.append({
            "platform_id": platform_id,
            "label": label,
            "de": max(anteriores).isoformat(),
            "para": atual.snapshot_date.isoformat(),
            "vitrine_antes": len(vitrine_anterior),
            "vitrine_agora": len(funds_hoje),
            "entradas": len(entradas),
            "saidas": len(saidas),
            # Desempate pelo nome: varias categorias com saldo -1 e o caso
            # comum, e so o valor absoluto nao decide quais seis ficam.
            "saldo_por_categoria": dict(
                sorted(por_categoria.items(), key=lambda kv: (-abs(kv[1]), kv[0]))[:6]
            ),
            "exemplos_entrada": [f.name[:70] for f in entradas[:5]],
        })

    return {
        "available": bool(movimentos),
        "reason": (
            None if movimentos else
            "nenhuma casa tem duas coletas de VITRINE para comparar - a série começa nesta execução"
        ),
        "movimentos": sorted(movimentos, key=lambda m: -(m["entradas"] - m["saidas"])),
        "sem_base": sem_base,
        "excluded": excluded,
    }


# --- 4. decisoes para a XP ----------------------------------------------------


@dataclass
class Decision:
    """Uma recomendacao com o numero que a sustenta e o custo de executa-la."""

    titulo: str
    tese: str
    evidencia: str
    tamanho: str
    acao: str
    esforco: str  # "operacional" | "comercial" | "analise"
    confianca: str  # "alta" | "media"

    def to_dict(self) -> dict:
        return {
            "titulo": self.titulo, "tese": self.tese, "evidencia": self.evidencia,
            "tamanho": self.tamanho, "acao": self.acao, "esforco": self.esforco,
            "confianca": self.confianca,
        }


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    if value >= 1_000_000:
        return f"R$ {value / 1_000_000:.1f} mi"
    if value >= 1_000:
        return f"R$ {value / 1_000:.0f} mil"
    return f"R$ {value:.0f}"


def _decisao_consenso(
    base: list[FundOffer],
    concorrentes: dict[str, list[FundOffer]],
    base_cnpjs: set[str],
    base_managers: set[str],
    baseline_id: str,
    snapshots: list[ShelfSnapshot],
    vitrines: dict[str, list[FundOffer]],
) -> list[Decision]:
    """Fundo que varias concorrentes exibem e a XP nao.

    Extraida de `build_xp_decisions`, que reunia as cinco num corpo de 274
    linhas. Cada decisao e independente: le a vitrine, decide se tem o que
    dizer, e devolve zero ou uma `Decision`.
    """
    decisoes: list[Decision] = []
    # (a) Consenso: fundo que varias concorrentes exibem e a XP nao. So entram as
    # concorrentes cuja vitrine da para cruzar por CNPJ - com a do Itau a 6%, o
    # "2 de 3 concorrentes" seria contado sobre uma casa que quase nao participa.
    cruzaveis, sem_identidade = identifiable_vitrines(concorrentes)
    minimo_casas = max(2, len(cruzaveis) - 1) if cruzaveis else 0
    presenca: dict[str, set[str]] = {}
    fundo_por_cnpj: dict[str, FundOffer] = {}
    for pid, funds in cruzaveis.items():
        for fund in funds:
            if not fund.cnpj:
                continue
            presenca.setdefault(fund.cnpj, set()).add(pid)
            fundo_por_cnpj.setdefault(fund.cnpj, fund)

    consenso = [
        (cnpj, casas) for cnpj, casas in presenca.items()
        if cnpj not in base_cnpjs and len(casas) >= minimo_casas
    ] if cruzaveis else []
    if consenso:
        ja_distribuida = [
            (c, casas) for c, casas in consenso
            if assinatura_da_gestora(_offer_manager(fundo_por_cnpj[c])) in base_managers
        ]
        exemplos = sorted(consenso, key=lambda item: -len(item[1]))[:5]
        decisoes.append(Decision(
            titulo="Fundos que a concorrência exibe em consenso e a XP não",
            tese=(
                "Um fundo na vitrine de várias casas ao mesmo tempo já passou pelo "
                "crivo comercial de todas elas - é o sinal mais barato de demanda "
                "validada que existe nesta base."
            ),
            evidencia=(
                f"{len(consenso)} fundos aparecem na vitrine de pelo menos "
                f"{minimo_casas} das {len(cruzaveis)} concorrentes cruzáveis por CNPJ "
                f"({', '.join(_platform_label(p) for p in cruzaveis)}) e estão fora da "
                "vitrine da XP. Exemplos: "
                + "; ".join(
                    f"{fundo_por_cnpj[c].name[:44]} ({len(casas)} casas)"
                    for c, casas in exemplos
                )
                + (
                    ". Fora da conta: "
                    + "; ".join(f"{e['label']} ({e['reason']})" for e in sem_identidade)
                    if sem_identidade else ""
                )
            ),
            tamanho=f"{len(consenso)} fundos",
            acao=(
                f"Começar pelos {len(ja_distribuida)} cuja gestora a XP já distribui - "
                "inclusão operacional, sem negociação nova. Os "
                f"{len(consenso) - len(ja_distribuida)} restantes exigem abrir gestora."
            ),
            esforco="operacional" if ja_distribuida else "comercial",
            confianca="alta",
        ))

    return decisoes


def _decisao_subindexacao(
    base: list[FundOffer],
    concorrentes: dict[str, list[FundOffer]],
    base_cnpjs: set[str],
    base_managers: set[str],
    baseline_id: str,
    snapshots: list[ShelfSnapshot],
    vitrines: dict[str, list[FundOffer]],
) -> list[Decision]:
    """Categoria em que a vitrine da XP esta rasa.

    Extraida de `build_xp_decisions`, que reunia as cinco num corpo de 274
    linhas. Cada decisao e independente: le a vitrine, decide se tem o que
    dizer, e devolve zero ou uma `Decision`.
    """
    decisoes: list[Decision] = []
    # (b) Sub-indexacao por categoria, com o tamanho da lacuna em fundos.
    posicao = build_positioning(snapshots)
    if posicao.get("available"):
        base_pos = next(
            (p for p in posicao["_positions"] if p.platform_id == baseline_id), None
        )
        if base_pos and base_pos.under_index:
            pior = base_pos.under_index[0]
            faltam = max(1, round((pior.peer_share - pior.share) * base_pos.vitrine))
            decisoes.append(Decision(
                titulo=f"Vitrine sub-indexada em {pior.category}",
                tese=(
                    "Categoria em que as concorrentes dedicam mais espaço de vitrine "
                    "que a XP. Espaço de vitrine é escolha editorial: quando quatro "
                    "casas dedicam mais que a quinta, ou elas viram demanda que a "
                    "quinta não viu, ou a quinta decidiu não competir ali - e vale "
                    "saber qual das duas."
                ),
                evidencia=(
                    f"{pior.category}: {pior.share:.1%} da vitrine da XP contra "
                    f"{pior.peer_share:.1%} nas concorrentes (índice {pior.index:.2f})."
                    # A clausula so aparece quando ha outras - senao a evidencia
                    # terminava com "Outras sub-indexadas:" e nada depois.
                    + (
                        " Outras sub-indexadas: "
                        + "; ".join(
                            f"{b.category} ({b.index:.2f}x)"
                            for b in base_pos.under_index[1:4]
                        )
                        + "."
                        if len(base_pos.under_index) > 1
                        else " É a única classe em que a XP fica abaixo das concorrentes."
                    )
                ),
                tamanho=f"~{faltam} fundos para igualar o share das concorrentes",
                acao=(
                    "Decidir explicitamente entre cobrir a categoria ou registrar que "
                    "a ausência é estratégica - hoje não há registro de qual é o caso."
                ),
                esforco="analise",
                confianca="alta",
            ))

    return decisoes


def _decisao_acessibilidade(
    base: list[FundOffer],
    concorrentes: dict[str, list[FundOffer]],
    base_cnpjs: set[str],
    base_managers: set[str],
    baseline_id: str,
    snapshots: list[ShelfSnapshot],
    vitrines: dict[str, list[FundOffer]],
) -> list[Decision]:
    """Ticket de entrada, so sobre minimo da propria vitrine.

    Extraida de `build_xp_decisions`, que reunia as cinco num corpo de 274
    linhas. Cada decisao e independente: le a vitrine, decide se tem o que
    dizer, e devolve zero ou uma `Decision`.
    """
    decisoes: list[Decision] = []
    # (c) Acessibilidade: so sobre minimo publicado PELA VITRINE (ver
    # vitrine_only_values). Quando o baseline nao publica, a conclusao muda de
    # "ticket mais alto" para "ticket nao exibido" - que e outra decisao.
    base_mins = vitrine_only_values(base, "min_initial")
    pares = {
        pid: vitrine_only_values(funds, "min_initial")
        for pid, funds in concorrentes.items()
    }
    pares = {pid: vals for pid, vals in pares.items() if len(vals) >= _MIN_TICKET_SAMPLE}
    peer_mins = [v for vals in pares.values() for v in vals]

    if pares and len(base_mins) < _MIN_TICKET_SAMPLE:
        peer_median = _median(peer_mins)
        peer_retail = sum(1 for v in peer_mins if v <= _RETAIL_TICKET) / len(peer_mins)
        # Sem minimo da vitrine da XP, o que falta e DADO da coleta, nao uma
        # decisao de produto: em 2026-09-19 esta decisao recomendou a XP "exibir
        # o ticket na listagem", e a lista da XP ja exibia - o coletor e que nao
        # lia o campo. Ela so pode apontar o buraco do monitor.
        decisoes.append(Decision(
            titulo="Sem o ticket da vitrine da XP na coleta: acessibilidade sem comparação",
            tese=(
                "Aplicação mínima é a primeira barreira que o investidor de varejo "
                "encontra, antes de qualquer comparação de rentabilidade. Sem o mínimo "
                "que a própria vitrine da XP exibe, a porta de entrada dela não pode "
                "ser comparada com a das concorrentes - nem para dizer que é maior, "
                "nem para dizer que é menor."
            ),
            evidencia=(
                f"Só {len(base_mins)} dos {len(base)} fundos da vitrine da XP têm "
                "mínimo lido da própria vitrine, abaixo dos "
                f"{_MIN_TICKET_SAMPLE} necessários; nos demais o mínimo que existe é o "
                "do registro Open Finance, o institucional da classe, não o ticket que "
                "a tela mostra. "
                + "; ".join(
                    f"{_platform_label(pid)} publica em {len(vals)} fundos "
                    f"(mediana {_fmt_money(_median(vals))}, "
                    f"{sum(1 for v in vals if v <= _RETAIL_TICKET) / len(vals):.0%} "
                    f"até {_fmt_money(_RETAIL_TICKET)})"
                    for pid, vals in pares.items()
                )
                + f". Somadas, as concorrentes têm mediana {_fmt_money(peer_median)} e "
                f"{peer_retail:.0%} da vitrine acessível por até "
                f"{_fmt_money(_RETAIL_TICKET)}."
            ),
            tamanho=(
                f"{len(base_mins)} de {len(base)} fundos da vitrine da XP com ticket "
                "da própria vitrine na coleta"
            ),
            acao=(
                "Monitor, não produto: conferir por que o coletor da XP não trouxe a "
                "aplicação mínima da lista. Até la, não há base para afirmar que o "
                "ticket da XP é maior nem menor que o das concorrentes."
            ),
            esforco="analise",
            confianca="alta",
        ))
    elif len(base_mins) >= _MIN_TICKET_SAMPLE and pares:
        base_median = _median(base_mins)
        peer_median = _median(peer_mins)
        base_retail = sum(1 for v in base_mins if v <= _RETAIL_TICKET) / len(base_mins)
        peer_retail = sum(1 for v in peer_mins if v <= _RETAIL_TICKET) / len(peer_mins)
        if base_median is not None and peer_median is not None and base_median > peer_median:
            decisoes.append(Decision(
                titulo="Ticket de entrada acima das concorrentes",
                tese=(
                    "Aplicação mínima é a primeira barreira que o investidor de varejo "
                    "encontra, antes de qualquer comparação de rentabilidade."
                ),
                evidencia=(
                    f"Mediana da vitrine da XP em {_fmt_money(base_median)} contra "
                    f"{_fmt_money(peer_median)} nas concorrentes que publicam na própria "
                    f"vitrine ({', '.join(_platform_label(p) for p in pares)}). Fundos "
                    f"acessíveis por até {_fmt_money(_RETAIL_TICKET)}: {base_retail:.0%} "
                    f"na XP contra {peer_retail:.0%} nelas. Comparação restrita ao mínimo "
                    "publicado pela própria vitrine."
                ),
                tamanho=(
                    f"{base_retail:.0%} contra {peer_retail:.0%} de vitrine acessível a "
                    f"{_fmt_money(_RETAIL_TICKET)}"
                ),
                acao=(
                    "Checar se os fundos de maior giro têm classe de mínimo menor "
                    "disponível - o mesmo fundo com outra classe resolve sem produto novo."
                ),
                esforco="operacional",
                confianca="media",
            ))

    return decisoes


def _decisao_gestoras(
    base: list[FundOffer],
    concorrentes: dict[str, list[FundOffer]],
    base_cnpjs: set[str],
    base_managers: set[str],
    baseline_id: str,
    snapshots: list[ShelfSnapshot],
    vitrines: dict[str, list[FundOffer]],
) -> list[Decision]:
    """Gestora com distribuicao ampla e ausente na XP.

    Extraida de `build_xp_decisions`, que reunia as cinco num corpo de 274
    linhas. Cada decisao e independente: le a vitrine, decide se tem o que
    dizer, e devolve zero ou uma `Decision`.
    """
    decisoes: list[Decision] = []
    # (d) Gestoras com distribuicao ampla e ausentes na XP.
    alcance = build_manager_reach(snapshots, baseline_id=baseline_id)
    if alcance.get("available") and alcance["_ausentes"]:
        ausentes = alcance["_ausentes"]
        top = ausentes[:5]
        decisoes.append(Decision(
            titulo="Gestoras distribuídas por várias concorrentes e ausentes na XP",
            tese=(
                "Gestora presente em múltiplas vitrines já resolveu due diligence, "
                "operacional e comercial em mais de uma casa - o custo de entrada "
                "para a próxima é menor que o de uma gestora sem distribuição."
            ),
            evidencia=(
                f"{len(ausentes)} gestoras aparecem em 2 ou mais vitrines concorrentes "
                "e em nenhuma posição da vitrine da XP. Maiores: "
                + "; ".join(f"{r.manager[:34]} ({r.reach} casas, {r.total_funds} fundos)" for r in top)
            ),
            tamanho=f"{len(ausentes)} gestoras",
            acao="Priorizar por número de casas e volume de fundos já distribuídos.",
            esforco="comercial",
            confianca="media",
        ))

    return decisoes


def _decisao_exclusivos(
    base: list[FundOffer],
    concorrentes: dict[str, list[FundOffer]],
    base_cnpjs: set[str],
    base_managers: set[str],
    baseline_id: str,
    snapshots: list[ShelfSnapshot],
    vitrines: dict[str, list[FundOffer]],
) -> list[Decision]:
    """O que so a XP tem - ativo a defender, nao gap.

    Extraida de `build_xp_decisions`, que reunia as cinco num corpo de 274
    linhas. Cada decisao e independente: le a vitrine, decide se tem o que
    dizer, e devolve zero ou uma `Decision`.
    """
    decisoes: list[Decision] = []
    # (e) O que so a XP tem - o ativo a defender, nao um gap a fechar.
    todas_cruzaveis, _ = identifiable_vitrines(vitrines)
    exclusivos = _exclusive_on_vitrine(todas_cruzaveis).get(baseline_id, set())
    if exclusivos and baseline_id in todas_cruzaveis:
        decisoes.append(Decision(
            titulo="Diferencial exclusivo da vitrine da XP",
            tese=(
                "O inverso do gap, é a metade da análise competitiva que costuma "
                "faltar: o que só a XP exibe e o que a concorrência ainda não copiou."
            ),
            evidencia=(
                f"{len(exclusivos)} fundos aparecem na vitrine da XP e em nenhuma das "
                f"{len(todas_cruzaveis) - 1} concorrentes cruzáveis por CNPJ "
                f"({len(exclusivos) / len(base):.0%} da vitrine). Uma casa que não "
                "publica CNPJ na vitrine não pode desmentir a exclusividade, então o "
                "número é um teto."
            ),
            tamanho=f"{len(exclusivos)} fundos exclusivos",
            acao=(
                "Usar como argumento comercial e monitorar quando um deles aparecer "
                "na vitrine concorrente - a perda de exclusividade é um evento."
            ),
            esforco="analise",
            confianca="alta",
        ))

    return decisoes


def build_xp_decisions(
    snapshots: list[ShelfSnapshot],
    baseline_id: str = "xp",
    suprimidos: dict[str, str] | None = None,
    cobertos: set[str] | None = None,
    distribuivel=None,
) -> dict:
    """Do diagnostico para a decisao: o que a XP faz na segunda-feira.

    Cada item traz o numero que o sustenta e o esforco que ele custa, porque uma
    lista de oportunidades sem custo nao e priorizavel: adicionar fundo de
    gestora ja distribuida e outra conversa que abrir gestora nova.

    Com BLOCO_CNPJ em `suprimidos` (previdencia), saem as duas decisoes que
    cruzam vitrines por CNPJ - consenso ("inclusao operacional") e exclusivos:
    com um veiculo por seguradora, as duas descrevem a seguradora.

    `cobertos` sao os CNPJs que a vitrine da XP cobre por espelho ou
    equivalente (`aggregate.cnpjs_cobertos`): o consenso nao os chama de
    lacuna, pela mesma regra do bloco de gaps. `distribuivel` poe as gestoras
    da lista da casa entre as que a XP ja distribui (`gestoras_da_casa`).
    """
    vitrines, excluded = vitrine_snapshots(snapshots)
    if baseline_id not in vitrines:
        motivo = next(
            (e["reason"] for e in excluded if e["platform_id"] == baseline_id),
            "vitrine ausente nesta execução",
        )
        return {
            "available": False,
            "reason": f"vitrine da {_platform_label(baseline_id)} indisponível: {motivo}",
            "excluded": excluded,
        }

    base = vitrines[baseline_id]
    concorrentes = {pid: fs for pid, fs in vitrines.items() if pid != baseline_id}
    if not concorrentes:
        return {
            "available": False,
            "reason": "nenhuma vitrine concorrente utilizável nesta execução",
            "excluded": excluded,
        }

    decisoes: list[Decision] = []
    base_cnpjs = {f.cnpj for f in base if f.cnpj}
    base_managers = gestoras_da_casa(base, snapshots, distribuivel)

    por_cnpj = (_decisao_consenso, _decisao_exclusivos)
    sem_cnpj = (suprimidos or {}).get(BLOCO_CNPJ)
    for construir in (
        _decisao_consenso,
        _decisao_subindexacao,
        _decisao_acessibilidade,
        _decisao_gestoras,
        _decisao_exclusivos,
    ):
        if sem_cnpj and construir in por_cnpj:
            continue
        # So o consenso ve os cobertos como "ja na XP": nas demais decisoes a
        # base continua sendo a vitrine literal.
        cnpjs = base_cnpjs | (cobertos or set()) if construir is _decisao_consenso else base_cnpjs
        decisoes.extend(construir(
            base, concorrentes, cnpjs, base_managers,
            baseline_id, snapshots, vitrines,
        ))

    return {
        "available": bool(decisoes),
        "reason": None if decisoes else "nenhum sinal acima do limiar nesta execução",
        "baseline_id": baseline_id,
        "baseline_label": _platform_label(baseline_id),
        "concorrentes": [_platform_label(p) for p in concorrentes],
        "excluded": excluded,
        "decisoes": [d.to_dict() for d in decisoes],
        "_decisoes": decisoes,
    }


def build_competitive_view(
    config: Config,
    snapshots: list[ShelfSnapshot],
    cobertos: set[str] | None = None,
    distribuivel=None,
) -> dict:
    """As quatro visoes, numa estrutura.

    `suprimidos` vem do Config (`blocos_suprimidos`), como no RunReport.
    `cobertos` vai para as decisoes (ver `build_xp_decisions`).
    """
    suprimidos = dict(config.blocos_suprimidos)
    return {
        "gerado_em": date.today().isoformat(),
        "suprimidos": suprimidos,
        "posicionamento": build_positioning(snapshots),
        "gestoras": build_manager_reach(snapshots),
        "momentum": build_momentum(config, snapshots),
        "decisoes": build_xp_decisions(
            snapshots, suprimidos=suprimidos, cobertos=cobertos, distribuivel=distribuivel
        ),
    }


# --- renderizacao no console --------------------------------------------------


def _fmt_index(index: float) -> str:
    return "só esta casa" if index == float("inf") else f"{index:.2f}x"


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


def print_competitive_view(view: dict) -> None:
    """Imprime as quatro visoes, cada uma com quem ficou de fora e por que."""
    from rich.console import Console
    from rich.table import Table

    console = Console()
    sem_cnpj = (view.get("suprimidos") or {}).get(BLOCO_CNPJ)

    # 1. posicionamento
    pos = view["posicionamento"]
    console.print()
    console.print("[bold]1. Assinatura de cada vitrine[/] - onde a casa aposta acima das demais")
    if not pos["available"]:
        console.print(f"   [yellow]{pos['reason']}[/]")
    else:
        table = Table(title="Posicionamento no eixo comparável (classe de ativo)", title_style="bold")
        table.add_column("Casa")
        table.add_column("Vitrine", justify="right")
        table.add_column("Aposta acima")
        table.add_column("Abaixo das demais")
        if not sem_cnpj:
            table.add_column("Só ela tem", justify="right")
        table.add_column("Gestoras", justify="right")
        table.add_column("Casa própria", justify="right")
        for p_ in pos["positions"]:
            so_ela = [] if sem_cnpj else [
                f"{p_['exclusive']} ({p_['exclusive_share']:.0%})"
                if p_["exclusive"] is not None else "s/CNPJ"
            ]
            table.add_row(
                p_["label"],
                str(p_["vitrine"]),
                ", ".join(f"{b['category']} {_fmt_index(b['index'])}" for b in p_["over_index"][:2]) or "-",
                ", ".join(f"{b['category']} {b['index']:.2f}x" for b in p_["under_index"][:2]) or "-",
                *so_ela,
                str(p_["distinct_managers"]) if p_["distinct_managers"] else "s/dado",
                _fmt_pct(p_["own_product_share"]),
            )
        console.print(table)
        if sem_cnpj:
            console.print(f"   [dim]sem \"só ela tem\" e sem decisões por CNPJ: {sem_cnpj}.[/]")

        finos = [p_ for p_ in pos["positions"] if p_["fine_over"] or p_["fine_under"]]
        if finos:
            fine = Table(title="Eixo fino ANBIMA - só casas com cobertura", title_style="bold")
            fine.add_column("Casa")
            fine.add_column("Classif.", justify="right")
            fine.add_column("Categorias em que lidera")
            for p_ in finos:
                fine.add_row(
                    p_["label"],
                    f"{p_['classificacao_cov']:.0%}",
                    ", ".join(f"{b['category']} {_fmt_index(b['index'])}" for b in p_["fine_over"][:3]) or "-",
                )
            console.print(fine)
            for p_ in pos["positions"]:
                if p_ not in finos:
                    console.print(
                        f"   [dim]{p_['label']} fora do eixo fino: cobertura ANBIMA de "
                        f"{p_['classificacao_cov']:.0%} - o resto da vitrine dela não "
                        f"casou com o cadastro por nenhuma via.[/]"
                    )
    for e in pos.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")

    # 2. gestoras
    ges = view["gestoras"]
    console.print()
    console.print("[bold]2. Gestoras[/] - quem ganhou distribuição, e onde a XP não está")
    if not ges["available"]:
        console.print(f"   [yellow]{ges['reason']}[/]")
    else:
        table = Table(title=f"Maior alcance entre as vitrines ({ges['total_gestoras']} gestoras)", title_style="bold")
        table.add_column("Gestora")
        table.add_column("Casas", justify="right")
        table.add_column("Fundos", justify="right")
        table.add_column("Distribuição")
        for r in ges["mais_distribuidas"][:8]:
            table.add_row(
                r["manager"][:42], str(r["reach"]), str(r["total_funds"]),
                ", ".join(f"{_platform_label(pid)} {n}" for pid, n in r["funds_por_casa"].items()),
            )
        console.print(table)
        ausentes = ges["ausentes_no_baseline"]
        if ausentes:
            console.print(
                f"   [yellow]{contagem(ges['total_ausentes_no_baseline'], 'gestora', 'gestoras')} "
                "em 2+ vitrines concorrentes e "
                f"{'ausente' if ges['total_ausentes_no_baseline'] == 1 else 'ausentes'} na XP:[/] "
                + "; ".join(f"{r['manager'][:30]} ({r['reach']} casas)" for r in ausentes[:5])
            )
    for e in ges.get("excluded", []):
        console.print(f"   [dim]fora: {e['label']} - {e['reason']}[/]")

    # 3. momentum
    mom = view["momentum"]
    console.print()
    console.print("[bold]3. Momentum[/] - o que cada casa mexeu na vitrine")
    for mv in mom.get("movimentos", []):
        saldo = mv["entradas"] - mv["saidas"]
        cor = "green" if saldo > 0 else "red" if saldo < 0 else "dim"
        console.print(
            f"   [{cor}]{mv['label']}[/]: {mv['vitrine_antes']} -> {mv['vitrine_agora']} fundos "
            f"({mv['de']} a {mv['para']}), +{mv['entradas']} / -{mv['saidas']}"
        )
        if mv["saldo_por_categoria"]:
            console.print(
                "      saldo por classe: "
                + ", ".join(f"{cat} {n:+d}" for cat, n in mv["saldo_por_categoria"].items())
            )
    for s in mom.get("sem_base", []):
        console.print(f"   [dim]{s['label']}: {s['reason']}[/]")
    if not mom.get("movimentos"):
        console.print(f"   [yellow]{mom.get('reason') or 'sem série comparável'}[/]")

    # 4. decisoes
    dec = view["decisoes"]
    console.print()
    console.print("[bold]4. Decisões para a XP[/] - o que fazer, com o número que sustenta")
    if not dec["available"]:
        console.print(f"   [yellow]{dec['reason']}[/]")
        return
    console.print(
        f"   [dim]baseline {dec['baseline_label']} contra "
        f"{', '.join(dec['concorrentes'])}[/]"
    )
    _ESFORCO = {"operacional": "green", "comercial": "yellow", "analise": "cyan"}
    for index, d in enumerate(dec["decisoes"], 1):
        cor = _ESFORCO.get(d["esforco"], "white")
        console.print()
        console.print(
            f"   [bold]{index}. {d['titulo']}[/]  "
            f"[{cor}]{d['esforco']}[/] / confiança {d['confianca']}"
        )
        console.print(f"      [bold]tamanho:[/] {d['tamanho']}")
        console.print(f"      [dim]tese:[/] {d['tese']}")
        console.print(f"      [dim]evidência:[/] {d['evidencia']}")
        console.print(f"      [bold]ação:[/] {d['acao']}")
