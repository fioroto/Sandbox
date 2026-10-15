"""Agregacoes competitivas derivadas dos snapshots - insumo dos relatorios."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.config import PLATFORMS, WATCHLIST
from shelf_monitor.distribuivel import PODE_DISTRIBUIR, BaseDistribuivel
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM, KEY_GESTOR_OFICIAL, KEY_PL
from shelf_monitor.familias import (
    COBERTO_EQUIVALENTE,
    COBERTO_ESPELHO,
    GAP_ESTRATEGIA,
    GAP_GESTORA,
    OUTRA_LIQUIDEZ,
    PRESENTE,
    ROTULOS_VEREDITO,
    SEM_DADO,
    MapaDeFamilias,
)
from shelf_monitor.normalize import format_cnpj, name_slug, normalize_text
from shelf_monitor.profile import split_by_profile
from shelf_monitor.texto import competencia_br, contagem, data_br, milhar
from shelf_monitor.report.shared import (
    MIN_AMOSTRA_TICKET,
    mediana,
    movimentos_de_vitrine,
    ordem_canonica,
    quartis,
    rotulo_da_casa,
)
from shelf_monitor.models import (
    BLOCO_CNPJ,
    AssetClass,
    FundOffer,
    RunReport,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)

ASSET_CLASS_LABELS: dict[AssetClass, str] = {
    AssetClass.RENDA_FIXA: "Renda Fixa",
    AssetClass.MULTIMERCADO: "Multimercado",
    AssetClass.ACOES: "Ações",
    AssetClass.CAMBIAL: "Cambial",
    AssetClass.INTERNACIONAL: "Internacional",
    AssetClass.PREVIDENCIA: "Previdência",
    AssetClass.OUTROS: "Outros",
}

# Faixas de aplicacao minima - separam varejo de alta renda e private.
MIN_BANDS: list[tuple[str, float, float]] = [
    ("Até R$ 100", 0.0, 100.0),
    ("R$ 100 a 1 mil", 100.0, 1_000.0),
    ("R$ 1 mil a 5 mil", 1_000.0, 5_000.0),
    ("R$ 5 mil a 25 mil", 5_000.0, 25_000.0),
    ("Acima de R$ 25 mil", 25_000.0, float("inf")),
]


def band_for(value: float | None) -> str | None:
    if value is None:
        return None
    for label, low, high in MIN_BANDS:
        if low <= value < high:
            return label
    return MIN_BANDS[-1][0]


@dataclass
class PlatformSummary:
    """Uma linha da matriz competitiva de prateleira - medida na VITRINE.

    Os tres numeros de tamanho existem porque sao tres perguntas diferentes, e
    mistura-los foi o artefato que este bloco veio corrigir:

    - `listing_funds`: fundos de varejo na vitrine da casa. E a UNICA base de
      toda comparacao entre casas. Tudo que este objeto descreve (classes,
      gestoras, faixas de minimo, taxa mediana, exclusivos) sai daqui.
    - `catalog_funds`: fundos de varejo que a casa administra, vitrine mais
      catalogo Open Finance. Descreve a casa como administradora, nao como
      distribuidora. Aparece rotulado, nunca num eixo comparativo.
    - `collected_funds`: tudo que a coleta trouxe, restritos inclusive. Serve a
      saude da coleta e a mais nada.

    Medido em 2026-09-20, com os tres colapsados num "total": o BTG aparecia com
    884 gestoras contra 213 da XP, 5.767 fundos exclusivos contra 284, e taxa
    mediana de 0,75% contra 1,40%. Nenhum desses numeros era sobre a prateleira
    do BTG - eram sobre os 9.413 fundos que so o Open Finance dele publica, o
    que a casa administra e o cliente nunca ve. Na vitrine as mesmas casas ficam
    em 167 contra 169 gestoras e 284 contra 337 exclusivos, que e a comparacao
    que alguem queria fazer.

    Casa sem vitrine alcancavel (o Nu, hoje) sai com `listing_funds = 0` e
    `listing_reason` preenchido: fica fora dos graficos com motivo declarado,
    em vez de entrar neles medida pelo catalogo.
    """

    platform_id: str
    label: str
    status: str
    strategy: str
    error: str | None
    listing_funds: int
    catalog_funds: int = 0
    collected_funds: int = 0
    listing_reason: str | None = None
    by_class: dict[str, int] = field(default_factory=dict)
    distinct_managers: int = 0
    top_managers: list[tuple[str, int]] = field(default_factory=list)
    min_bands: dict[str, int] = field(default_factory=dict)
    lowest_min: float | None = None
    median_admin_fee: float | None = None
    exclusive_count: int = 0
    cnpj_coverage: float = 0.0

    def to_dict(self) -> dict:
        return {
            "platform_id": self.platform_id,
            "label": self.label,
            "status": self.status,
            "strategy": self.strategy,
            "error": self.error,
            "listing_funds": self.listing_funds,
            "catalog_funds": self.catalog_funds,
            "collected_funds": self.collected_funds,
            "listing_reason": self.listing_reason,
            "by_class": self.by_class,
            "distinct_managers": self.distinct_managers,
            "top_managers": [{"name": n, "count": c} for n, c in self.top_managers],
            "min_bands": self.min_bands,
            "lowest_min": self.lowest_min,
            "median_admin_fee": self.median_admin_fee,
            "exclusive_count": self.exclusive_count,
            "cnpj_coverage": round(self.cnpj_coverage, 3),
        }


_median = mediana
_quartiles = quartis


def _own_product_patterns(platform_id: str) -> list[re.Pattern] | None:
    watch = WATCHLIST.get(platform_id)
    if not watch:
        return None
    return [re.compile(pattern) for pattern in watch.name_patterns]


def is_own_product(platform_id: str, fund: FundOffer) -> bool | None:
    """O fundo e de gestora ligada a propria casa? None = sem gestor conhecido.

    Reusa os mesmos padroes da watchlist do radar CVM - a definicao de "gestora
    da casa" e uma so no monitor inteiro.
    """
    patterns = _own_product_patterns(platform_id)
    manager = fund.extra.get(KEY_GESTOR_OFICIAL) or fund.manager_name
    if not patterns or not manager:
        return None
    normalized = normalize_text(manager)
    return any(p.search(normalized) for p in patterns)


def categoria_oficial(fund: FundOffer) -> str | None:
    """Categoria do fundo vinda da base da CVM, ou None.

    Escrita em `categoria_oficial` por duas fontes, ambas o cadastro da CVM:
    o join por identidade (`enrich.enrich_offer`) e o resolver por nome
    (`shelf_monitor.classify`). Qual delas resolveu fica em `categoria_via`,
    entao a superficie pode mostrar procedencia em vez de um rotulo sem origem.

    Duas coisas NAO entram aqui, pelo mesmo motivo - nao sao lidas do cadastro:

    - a heuristica `AssetClass`, inferida do nome;
    - `classificacao_anbima` quando ela veio da propria casa. Os coletores
      escrevem nessa chave o rotulo publicado pela plataforma, e misturar os
      dois da a cada casa um balde que so ela usa.
    """
    return fund.extra.get(KEY_CATEGORIA) or None


def categoria_comparavel(fund: FundOffer) -> str | None:
    """Rotulo do fundo no eixo fino comparavel entre casas. None = fora.

    Devolver None em vez de um rotulo de consolo e o ponto: um fundo sem
    categoria oficial nao pertence a uma comparacao de composicao de
    prateleira, e o bloco que o excluir tem de dizer quantos excluiu.
    """
    return categoria_oficial(fund)


def anbima_category(fund: FundOffer) -> str:
    """Categoria do fundo para os blocos descritivos, sempre com um rotulo.

    Difere de `categoria_comparavel` em aceitar duas fontes que aquela recusa:
    o rotulo publicado pela propria casa e a heuristica `AssetClass`. Listar um
    fundo do Itau como "Renda Fixa" numa tabela de inventario e util e
    inofensivo; o que nao se pode e usar esse rotulo para dizer que o Itau
    aposta mais em renda fixa que os pares - para isso existe
    `categoria_comparavel`, que so aceita o cadastro.
    """
    rotulo = (
        categoria_oficial(fund)
        or fund.extra.get(KEY_ANBIMA)
        or fund.extra.get(KEY_CVM)
    )
    if rotulo:
        return rotulo
    if fund.asset_class != AssetClass.OUTROS:
        return ASSET_CLASS_LABELS[fund.asset_class]
    return "Sem classificação"


def listing_funds(snapshot: ShelfSnapshot) -> list[FundOffer]:
    """A vitrine de varejo da casa - a base de toda comparacao entre casas.

    Publica de proposito: e a regra de pertinencia que o dashboard, o digest e
    a leitura competitiva precisam compartilhar. Qualquer bloco que compare
    casas e nao passe por aqui esta comparando o catalogo Open Finance de uma
    com a prateleira da outra.
    """
    return [f for f in retail_funds(snapshot) if f.on_listing]


# Alias interno: o modulo inteiro chamava assim antes de a funcao virar publica.
_listing_funds = listing_funds


def _listing_presence(snapshots: list[ShelfSnapshot]) -> dict[str, set[str]]:
    """CNPJ -> casas em cuja vitrine de varejo o fundo aparece.

    A regra de pertinencia (varejo + vitrine + CNPJ) mora aqui, uma unica vez:
    e a mesma para exclusivos, shared e gaps.
    """
    presence: dict[str, set[str]] = {}
    for snapshot in snapshots:
        for fund in _listing_funds(snapshot):
            if fund.cnpj:
                presence.setdefault(fund.cnpj, set()).add(snapshot.platform)
    return presence


# Quantas categorias ANBIMA aparecem por extenso na matriz; o resto agrega em
# "Outras" para a tabela caber numa tela de comite.
_MATRIX_CATEGORIES = 12

# Idem para o bloco de gaps, que tende a ter cauda longa de categorias.
_GAP_CATEGORIES = 15

# Fundos de gap embutidos no JSON do dashboard/deck; a planilha recebe a lista
# completa chamando build_gap_block diretamente.
_GAP_FUNDS_EMBEDDED = 150


# Abaixo deste minimo o fundo e porta de entrada de varejo, nao produto de
# alta renda. O corte e o topo da terceira faixa de MIN_BANDS.
_TICKET_VAREJO = 5_000.0


def leitura_do_ticket(platforms: list[dict], baseline_id: str = "xp") -> dict | None:
    """O que a distribuicao de aplicacao minima diz que a XP deve fazer.

    O bloco de faixas era um grafico de barras sem veredito: mostrava a forma
    da prateleira de cada casa e parava ai. O veredito e calculado aqui para as
    quatro superficies dizerem a mesma coisa.

    Duas regras, e a falta delas publicou uma conclusao falsa. As faixas saem so
    do minimo que a vitrine mostra (`FundOffer.minimo_vitrine`), e cada casa
    precisa de `MIN_AMOSTRA_TICKET` fundos com esse minimo para entrar. Em
    2026-09-21 o relatorio disse que "a vitrine da XP nao tem nenhum fundo
    abaixo de R$ 5 mil - e a ausencia de uma porta de entrada": as faixas da XP
    vinham do minimo institucional do Open Finance, porque o coletor nao lia o
    minimo da lista. Conferido na propria lista em 2026-09-23, 80% da vitrine da
    XP esta abaixo de R$ 5 mil.

    Devolve None quando nao ha o que concluir - baseline ausente, ou nenhuma
    concorrente com amostra. Baseline sem amostra devolve `sem_base=True`: o
    que falta e dado da coleta, e a leitura diz isso em vez de concluir.
    """
    faixas_varejo = [rotulo for rotulo, _, teto in MIN_BANDS if teto <= _TICKET_VAREJO]

    def bandas(p: dict) -> dict:
        return p.get("min_bands") or p.get("bands") or {}

    def amostra(p: dict) -> int:
        return sum(bandas(p).values())

    def conta_varejo(p: dict) -> int:
        return sum(bandas(p).get(rotulo, 0) for rotulo in faixas_varejo)

    baseline = next((p for p in platforms if p["platform_id"] == baseline_id), None)
    if baseline is None:
        return None
    pares = [
        p for p in platforms
        if p["platform_id"] != baseline_id and amostra(p) >= MIN_AMOSTRA_TICKET
    ]
    if not pares:
        return None

    melhor = max(pares, key=conta_varejo)
    sem_base = amostra(baseline) < MIN_AMOSTRA_TICKET
    return {
        "baseline_id": baseline_id,
        "baseline_label": baseline["label"],
        "baseline_amostra": amostra(baseline),
        "baseline_varejo": None if sem_base else conta_varejo(baseline),
        "sem_base": sem_base,
        "amostra_minima": MIN_AMOSTRA_TICKET,
        "melhor_label": melhor["label"],
        "melhor_varejo": conta_varejo(melhor),
        "teto": _TICKET_VAREJO,
        "faixas": faixas_varejo,
    }


def build_listing_block(report: RunReport) -> dict:
    """A visao de vitrine: so o que o varejo ve em cada casa.

    E deliberadamente separada dos totais por snapshot - o Open Finance lista o
    que a casa administra, e misturar os dois faria o catalogo passar por
    prateleira.
    """
    usable = [
        s
        for s in report.snapshots
        if s.status != SnapshotStatus.UNAVAILABLE and s.funds
    ]

    presence = _listing_presence(usable)
    platforms: list[dict] = []
    category_totals: Counter = Counter()

    for snapshot in usable:
        listing = _listing_funds(snapshot)
        if not listing:
            platforms.append(
                {
                    "platform_id": snapshot.platform,
                    "label": _platform_label(snapshot.platform),
                    "total": 0,
                    "reason": "sem listagem pública alcançável nesta coleta",
                }
            )
            continue

        by_category: Counter = Counter(anbima_category(f) for f in listing)
        category_totals.update(by_category)

        own = sum(1 for f in listing if is_own_product(snapshot.platform, f) is True)
        third = sum(1 for f in listing if is_own_product(snapshot.platform, f) is False)
        managers = {
            (f.extra.get(KEY_GESTOR_OFICIAL) or f.manager_name or "").strip()
            for f in listing
            if f.extra.get(KEY_GESTOR_OFICIAL) or f.manager_name
        }
        exclusive = sum(
            1
            for f in listing
            if f.cnpj and presence.get(f.cnpj) == {snapshot.platform}
        )

        platforms.append(
            {
                "platform_id": snapshot.platform,
                "label": _platform_label(snapshot.platform),
                "total": len(listing),
                "catalog_total": len(retail_funds(snapshot)),
                "by_category": dict(by_category),
                "own_product": own,
                "third_party": third,
                "distinct_managers": len(managers),
                "exclusive": exclusive,
                "fee_quartiles": _quartiles(
                    [f.admin_fee_pct for f in listing if f.admin_fee_pct is not None]
                ),
                # So o minimo que a vitrine mostra - ver FundOffer.minimo_vitrine.
                "min_quartiles": _quartiles(
                    [m for f in listing if (m := f.minimo_vitrine) is not None]
                ),
                "cnpj_coverage": round(
                    sum(1 for f in listing if f.cnpj) / len(listing), 3
                ),
            }
        )

    top_categories = [c for c, _ in category_totals.most_common(_MATRIX_CATEGORIES)]
    matrix: list[dict] = []
    for category in top_categories:
        matrix.append(
            {
                "category": category,
                "counts": {
                    p["platform_id"]: p.get("by_category", {}).get(category, 0)
                    for p in platforms
                },
            }
        )
    other = {
        p["platform_id"]: sum(
            n
            for cat, n in p.get("by_category", {}).items()
            if cat not in top_categories
        )
        for p in platforms
    }
    if any(other.values()):
        matrix.append({"category": "Outras", "counts": other})

    return {
        "platforms": platforms,
        "matrix": matrix,
        "shared_all": sum(
            1
            for cnpjs in presence.values()
            if len(cnpjs) >= 2
        ),
    }


def retail_funds(snapshot: ShelfSnapshot) -> list[FundOffer]:
    """So o perfil de varejo - a materia-prima de toda analise principal."""
    return split_by_profile(snapshot.funds)[0]


def _exclusive_cnpjs(snapshots: list[ShelfSnapshot]) -> dict[str, set[str]]:
    """CNPJs de varejo que aparecem na vitrine de exatamente uma casa.

    E o conceito COMPETITIVO ("so esta casa oferta"), nao o regulatorio
    ("fundo exclusivo de um investidor") - os regulatorios nem entram aqui.
    So considera snapshots utilizaveis: uma coleta que falhou tornaria
    diferencial todo fundo das demais casas.

    Vitrine dos dois lados, pela mesma razao que os gaps: contado sobre o
    catalogo, o BTG saia com 5.767 "diferenciais de prateleira" contra 284 da
    XP. Quase nenhum era diferencial - eram fundos que so ele administra e
    ninguem, nem o cliente dele, ve numa prateleira. Na vitrine sao 284 contra
    337, e ai a palavra "diferencial" significa o que diz.
    """
    usable = [s for s in snapshots if s.status != SnapshotStatus.UNAVAILABLE and s.funds]
    presence = _listing_presence(usable)

    exclusive: dict[str, set[str]] = {s.platform: set() for s in usable}
    for cnpj, platforms in presence.items():
        if len(platforms) == 1:
            exclusive[next(iter(platforms))].add(cnpj)
    return exclusive


# Alias de compatibilidade: seis modulos importavam este nome privado daqui.
# A definicao agora vive em `report.shared.plataformas`, publica.
_platform_label = rotulo_da_casa


_canonical_order = ordem_canonica


def _offer_manager(fund: FundOffer) -> str | None:
    manager = fund.extra.get(KEY_GESTOR_OFICIAL) or fund.manager_name
    return manager.strip() if manager else None


def _gap_funds(
    competitor_snaps: list[ShelfSnapshot],
    baseline_cnpjs: set[str],
    baseline_managers: set[str],
    baseline_slugs: dict[str, str],
) -> list[dict]:
    """A lista nominal de gaps, ordenada por presenca e PL.

    Extraida de `build_gap_block`, que reunia universo, lista nominal e
    cobertura por categoria em 251 linhas. As tres sao etapas distintas: esta
    responde QUAIS fundos faltam, a de categoria responde ONDE a vitrine esta
    rasa, e o universo decide quem entra na conta.

    As duas colunas heuristicas (`manager_on_xp`, `possible_xp_equivalent`)
    informam e nunca removem um fundo da lista.
    """
    # Exemplares por CNPJ de gap: (casa, fundo) de cada vitrine concorrente.
    offers_by_cnpj: dict[str, list[tuple[str, FundOffer]]] = {}
    for snapshot in competitor_snaps:
        for fund in _listing_funds(snapshot):
            if fund.cnpj and fund.cnpj not in baseline_cnpjs:
                offers_by_cnpj.setdefault(fund.cnpj, []).append(
                    (snapshot.platform, fund)
                )

    funds: list[dict] = []
    for cnpj, offers in offers_by_cnpj.items():
        houses = _canonical_order({platform for platform, _ in offers})
        # Exemplar de maior confianca: classificacao oficial primeiro, depois
        # quem apareceu em mais fontes da propria casa.
        rep = max(
            (fund for _, fund in offers),
            key=lambda f: (
                bool(f.extra.get(KEY_ANBIMA) or f.extra.get(KEY_CVM)),
                len(f.sources),
            ),
        )
        category = anbima_category(rep)

        manager = next(
            (m for m in (_offer_manager(f) for _, f in offers) if m), None
        )
        minimums = [m for _, f in offers if (m := f.minimo_vitrine) is not None]
        fees = [f.admin_fee_pct for _, f in offers if f.admin_fee_pct is not None]
        pl = next(
            (
                f.extra.get(KEY_PL)
                for _, f in offers
                if f.extra.get(KEY_PL) is not None
            ),
            None,
        )
        funds.append(
            {
                "cnpj": cnpj,
                "cnpj_display": format_cnpj(cnpj),
                "name": rep.name,
                "category": category,
                "manager": manager,
                "houses": houses,
                "houses_label": ", ".join(_platform_label(h) for h in houses),
                "houses_count": len(houses),
                "min_initial": min(minimums) if minimums else None,
                "admin_fee_pct": min(fees) if fees else None,
                "pl_cvm": pl,
                "manager_on_xp": (
                    normalize_text(manager) in baseline_managers
                    if manager
                    else None
                ),
                "possible_xp_equivalent": baseline_slugs.get(name_slug(rep.name)),
            }
        )

    funds.sort(key=lambda f: (-f["houses_count"], -(f["pl_cvm"] or 0), f["name"]))

    return funds


def _gap_categories(
    baseline_listing: list[FundOffer],
    competitor_snaps: list[ShelfSnapshot],
    funds: list[dict],
    cobertos: Counter | None = None,
) -> list[dict]:
    """Cobertura por categoria: vitrine do baseline contra cada concorrente.

    Responde ONDE a vitrine esta rasa, que e outra pergunta da lista nominal -
    uma categoria pode ter poucos gaps e ainda assim ser a maior lacuna
    relativa.

    A contagem de gaps por categoria e derivada de `funds` (os gaps reais) em
    vez de recebida pronta: era uma variavel que as duas etapas partilhavam
    dentro do corpo de 251 linhas, e passa-la adiante manteria o acoplamento
    que a extracao veio desfazer. `cobertos` conta, por categoria, os gaps
    aparentes que a base cobre por espelho ou equivalente.
    """
    cobertos = cobertos or Counter()
    gap_categories: Counter = Counter(f["category"] for f in funds)
    # Cobertura por categoria: vitrine XP vs. vitrine de cada concorrente.
    baseline_by_category: Counter = Counter(
        anbima_category(f) for f in baseline_listing
    )
    competitor_by_category: dict[str, Counter] = {
        s.platform: Counter(anbima_category(f) for f in _listing_funds(s))
        for s in competitor_snaps
    }

    # Desempate pelo nome. `Counter.most_common` mantem a ordem de insercao
    # entre empatados, entao a fatia dependia de em que ordem os CNPJs foram
    # percorridos la atras - duas categorias com o mesmo numero de gaps
    # trocavam de lugar ao refatorar, sem nada ter mudado no dado.
    top_categories = [
        c for c, _ in sorted(gap_categories.items(), key=lambda kv: (-kv[1], kv[0]))
    ][:_GAP_CATEGORIES]
    categories: list[dict] = []
    for category in top_categories:
        by_platform = {
            pid: counter.get(category, 0)
            for pid, counter in competitor_by_category.items()
        }
        best_id = max(by_platform, key=lambda pid: by_platform[pid])
        categories.append(
            {
                "category": category,
                "xp": baseline_by_category.get(category, 0),
                "by_platform": by_platform,
                "best_competitor": {
                    "platform_id": best_id,
                    "label": _platform_label(best_id),
                    "count": by_platform[best_id],
                },
                "gap_count": gap_categories[category],
                "cobertos": cobertos.get(category, 0),
            }
        )
    other_gaps = sum(
        n for cat, n in gap_categories.items() if cat not in top_categories
    )
    if other_gaps:
        categories.append(
            {
                "category": "Outras",
                "xp": sum(
                    n
                    for cat, n in baseline_by_category.items()
                    if cat not in top_categories
                ),
                "by_platform": {
                    pid: sum(
                        n
                        for cat, n in counter.items()
                        if cat not in top_categories
                    )
                    for pid, counter in competitor_by_category.items()
                },
                "best_competitor": None,
                "gap_count": other_gaps,
                "cobertos": sum(
                    n for cat, n in cobertos.items() if cat not in top_categories
                ),
            }
        )

    return categories


def bloco_de_gaps(report: RunReport) -> dict:
    """`build_gap_block` do relatorio, ou o motivo quando o modulo o suprime.

    Previdencia: cada seguradora tem o proprio veiculo, e gap por CNPJ mede a
    seguradora (ver ConfigPrevidencia.blocos_suprimidos). `nao_se_aplica`
    separa isto da supressao por falta de dado desta execucao.
    """
    motivo = report.suprime(BLOCO_CNPJ)
    if motivo:
        return {"available": False, "reason": motivo, "nao_se_aplica": True}
    return build_gap_block(
        report.snapshots, familias=report.familias, distribuivel=report.distribuivel
    )


def _lente_da_vitrine(
    snapshots: list[ShelfSnapshot], baseline_id: str, label: str
) -> dict:
    """A vitrine da casa-base como base de cobertura, ou o motivo de nao ser.

    Sem a vitrine da XP, todo fundo concorrente pareceria gap - por isso o
    motivo, e nunca uma base vazia.
    """
    baseline = next((s for s in snapshots if s.platform == baseline_id), None)
    if baseline is None:
        return {
            "available": False, "snapshot": None, "listing": [],
            "reason": f"snapshot da {label} ausente desta execução",
        }
    if baseline.status == SnapshotStatus.UNAVAILABLE or not baseline.funds:
        detail = f": {baseline.error}" if baseline.error else ""
        return {
            "available": False, "snapshot": baseline, "listing": [],
            "reason": f"coleta da {label} indisponível{detail}",
        }
    listing = _listing_funds(baseline)
    if not listing:
        return {
            "available": False, "snapshot": baseline, "listing": [],
            "reason": (
                f"{label} sem vitrine coletada nesta execução "
                f"(estratégia: {baseline.strategy.value}); o catálogo "
                "Open Finance não conta como vitrine"
            ),
        }
    warning = None
    if baseline.status == SnapshotStatus.PARTIAL:
        detail = f" ({baseline.error})" if baseline.error else ""
        warning = f"coleta da {label} parcial{detail}; gaps podem estar superestimados"
    return {
        "available": True, "snapshot": baseline, "listing": listing,
        "reason": None, "warning": warning,
    }


def _veredito_contra(
    cnpj: str,
    base_cnpjs: set[str],
    familias: MapaDeFamilias | None,
    base_indexada,
) -> dict | None:
    """O veredito de um CNPJ contra uma base; None sem mapa e fora da base.

    O mesmo CNPJ na base e cobertura sem precisar de carteira nenhuma.
    """
    if cnpj in base_cnpjs:
        return {
            "tipo": PRESENTE, "rotulo": ROTULOS_VEREDITO[PRESENTE], "coberto": True,
            "gap_real": False, "confianca": "alta", "cobre": cnpj,
            "ligacao": "mesma classe",
        }
    if familias is None:
        return None
    return familias.cobertura(cnpj, base_indexada).to_dict()


# As etapas do funil, na ordem em que um gap aparente se resolve.
_ETAPAS_DO_FUNIL: tuple[tuple[str, str], ...] = (
    ("mesma_classe", PRESENTE),
    ("espelho", COBERTO_ESPELHO),
    ("equivalente", COBERTO_EQUIVALENTE),
    ("outra_liquidez", OUTRA_LIQUIDEZ),
    ("sem_dado", SEM_DADO),
    ("estrategia", GAP_ESTRATEGIA),
    ("gestora", GAP_GESTORA),
)


def _funil(vereditos: list[dict | None]) -> dict:
    """Gaps aparentes -> cobertos -> gaps reais, de uma lente.

    Sem veredito (mapa de familias ausente) todo gap aparente e real: e a
    conta de antes, CNPJ contra CNPJ, e `verificado` diz que foi so ela.
    """
    tipos = Counter((v or {}).get("tipo") for v in vereditos)
    funil = {chave: tipos.get(tipo, 0) for chave, tipo in _ETAPAS_DO_FUNIL}
    funil["aparentes"] = len(vereditos)
    funil["cobertos"] = funil["mesma_classe"] + funil["espelho"] + funil["equivalente"]
    funil["reais"] = funil["aparentes"] - funil["cobertos"] - funil["outra_liquidez"]
    funil["verificado"] = any(v is not None for v in vereditos) or not vereditos
    return funil


def _rotulo_da_base(gap_block: dict) -> str:
    label = gap_block.get("baseline_label") or "XP"
    if gap_block.get("base") == "distribuivel":
        return f"lista distribuível da {label}"
    return f"vitrine da {label}"


def frase_do_funil(gap_block: dict) -> str | None:
    """Gaps aparentes -> cobertos -> reais, numa frase. None sem o mapa.

    Escrita uma vez, como as frases da captacao: digest, dashboard, deck e
    planilha publicam esta mesma.
    """
    funil = gap_block.get("funil") or {}
    if not gap_block.get("available") or not (gap_block.get("carteira") or {}).get("available"):
        return None
    base = _rotulo_da_base(gap_block)
    aparentes = funil.get("aparentes", 0)
    if not aparentes:
        return f"Nenhum gap aparente: toda vitrine concorrente coletada está na {base}."
    partes = []
    if funil.get("mesma_classe"):
        partes.append(
            f"{contagem(funil['mesma_classe'], 'é a mesma classe', 'são a mesma classe')} "
            "publicada com outro CNPJ"
        )
    if funil.get("espelho"):
        partes.append(
            f"{contagem(funil['espelho'], 'é espelho', 'são espelho')} do mesmo master "
            f"de um fundo da {base}"
        )
    if funil.get("equivalente"):
        partes.append(
            f"{contagem(funil['equivalente'], 'tem', 'têm')} equivalente da mesma "
            "gestora com carteira equivalente"
        )
    if funil.get("outra_liquidez"):
        partes.append(
            f"{contagem(funil['outra_liquidez'], 'é coberto', 'são cobertos')} só em "
            "outra faixa de liquidez"
        )
    reais = funil.get("reais", 0)
    detalhe = ", ".join(
        texto for texto in (
            contagem(funil.get("estrategia", 0), "de estratégia", "de estratégia")
            if funil.get("estrategia") else "",
            contagem(funil.get("gestora", 0), "de gestora", "de gestora")
            if funil.get("gestora") else "",
            f"{milhar(funil['sem_dado'])} sem carteira que permita decidir"
            if funil.get("sem_dado") else "",
        ) if texto
    )
    abertura = (
        f"Dos {milhar(aparentes)} gaps aparentes (CNPJ em vitrine concorrente e fora "
        f"da {base})"
        if aparentes != 1 else
        f"Do único gap aparente (CNPJ em vitrine concorrente e fora da {base})"
    )
    if partes:
        cobertura = "; ".join(partes)
        fecho = f"{'sobra' if reais == 1 else 'sobram'} {contagem(reais, 'gap real', 'gaps reais')}"
    else:
        cobertura = "nenhum é coberto por espelho ou equivalente"
        fecho = (
            "é gap real" if reais == 1
            else f"são todos gaps reais ({milhar(reais)})"
        )
    return f"{abertura}, {cobertura}: {fecho}" + (f" ({detalhe})." if detalhe else ".")


def resumo_do_funil(gap_block: dict) -> str | None:
    """O funil em uma linha, para onde a frase inteira nao cabe (o subtitulo
    do slide tem uma linha e meia). Os numeros sao os de `frase_do_funil`."""
    if frase_do_funil(gap_block) is None:
        return None
    funil = gap_block["funil"]
    return (
        f"{contagem(funil['aparentes'], 'gap aparente', 'gaps aparentes')}: "
        f"{contagem(funil['cobertos'], 'já coberto', 'já cobertos')} por espelho ou "
        f"equivalente, {milhar(funil['outra_liquidez'])} só em outra liquidez - "
        f"{contagem(funil['reais'], 'gap real', 'gaps reais')} "
        f"(carteira da CVM de {competencia_br((gap_block['carteira'].get('competencias') or [''])[0])})"
    )


def notas_da_cobertura(gap_block: dict) -> list[str]:
    """Proveniencia do veredito de cobertura - as mesmas frases nas superficies."""
    notas: list[str] = []
    carteira = gap_block.get("carteira") or {}
    if carteira.get("available"):
        competencias = carteira.get("competencias") or []
        recuo = (
            f" (e {', '.join(competencia_br(c) for c in competencias[1:])} para "
            "classes ausentes dela)" if len(competencias) > 1 else ""
        )
        notas.append(
            "Espelho e equivalência pela carteira mensal da CVM (CDA) de "
            f"{competencia_br(competencias[0]) if competencias else '-'}{recuo}; "
            f"{milhar(carteira.get('com_carteira', 0))} dos "
            f"{milhar(gap_block.get('gaps_aparentes', 0))} gaps aparentes tinham "
            "carteira publicada. Espelho: 95% ou mais do PL em cotas de um único "
            "fundo. Equivalente: mesma gestora e categoria ANBIMA, no máximo 15% "
            "da carteira em faixas diferentes e ao menos 60% dos ativos em comum. "
            "Sem carteira que decida, o possível equivalente só informa."
        )
    elif carteira.get("aviso"):
        notas.append(f"Atenção: {carteira['aviso']}.")
    lente = (gap_block.get("lentes") or {}).get("distribuivel") or {}
    lista = gap_block.get("distribuivel") or {}
    if lente.get("available"):
        data = lista.get("data_referencia")
        arquivos = lista.get("arquivos") or []
        frase = (
            f"Lista distribuível: {contagem(lista.get('total', 0), 'classe', 'classes')} "
            f"de {contagem(len(arquivos), 'arquivo', 'arquivos')}"
            + (f", referência {data_br(data)}" if data else "")
        )
        invalidas = lista.get("invalidas") or []
        if invalidas:
            frase += (
                f"; {contagem(len(invalidas), 'linha recusada', 'linhas recusadas')} "
                "(arquivo e motivo na planilha)"
            )
        funil = lente.get("funil") or {}
        if gap_block.get("base") == "vitrine" and funil.get("aparentes"):
            frase += (
                f". Contra ela, {milhar(funil.get('cobertos', 0))} dos "
                f"{milhar(funil['aparentes'])} fundos da vitrine concorrente fora da "
                "lista estão cobertos"
            )
        notas.append(frase + ".")
    elif gap_block.get("distribuivel") is not None and lente.get("reason"):
        notas.append(f"Lista distribuível: {lente['reason']}.")
    return notas


def cnpjs_cobertos(gap_block: dict) -> set[str]:
    """CNPJs de gap aparente que a base cobre (mesma classe, espelho ou
    equivalente). Os outros blocos que dizem "a XP nao tem" - consenso na
    leitura competitiva, oportunidade de grade na captacao - excluem estes, para
    nenhum chamar de lacuna o que o bloco de gaps diz estar coberto."""
    return {f["cnpj"] for f in gap_block.get("cobertos") or [] if f.get("cnpj")}


def build_gap_block(
    snapshots: list[ShelfSnapshot],
    baseline_id: str | None = None,
    familias: MapaDeFamilias | None = None,
    distribuivel: BaseDistribuivel | None = None,
) -> dict:
    """Gaps de prateleira do baseline: o que as vitrines concorrentes ofertam
    de varejo e a XP nao cobre - por CNPJ e, com o mapa de familias, por
    estrategia.

    O universo e sempre a vitrine dos concorrentes (o catalogo Open Finance nao
    entra em nenhum ponto da conta). A base de cobertura tem duas lentes:

    - a vitrine da XP, como sempre: um fundo que a XP so tem via Open Finance
      conta como gap, porque o cliente nao o ve;
    - a lista distribuivel (pasta de CSVs), quando existe: o que a XP distribui
      ou pode distribuir. Um gap da vitrine coberto por ela vira "expor na
      vitrine" em vez de "incluir".

    Cada gap aparente recebe um veredito por lente (`familias.Veredito`):
    coberto por espelho ou equivalente sai da contagem de gaps reais e vai para
    `cobertos`, com a evidencia - nada some. Sem o mapa (carteira da CVM
    ausente) o bloco e o de antes, CNPJ contra CNPJ, com o aviso.

    Protecoes anti-falso-alarme, na mesma linha de _exclusive_cnpjs:
    - baseline ausente, indisponivel ou sem vitrine, e sem lista distribuivel ->
      bloco suprimido com motivo (tudo pareceria gap); com a lista, ela vira a
      base e o aviso diz que a vitrine faltou;
    - baseline PARTIAL com vitrine -> renderiza com aviso (gaps podem estar
      superestimados);
    - concorrente indisponivel ou sem vitrine -> fora do universo, listado em
      "excluded" com motivo (vitrine parcial so subestima gaps, entao PARTIAL
      permanece).
    """
    if baseline_id is None:
        baseline_id = next(
            (p.platform_id for p in PLATFORMS.values() if p.is_baseline), None
        )
    if baseline_id is None:
        return {"available": False, "reason": "nenhuma casa baseline configurada"}

    label = _platform_label(baseline_id)
    vitrine = _lente_da_vitrine(snapshots, baseline_id, label)
    lista_motivo = (
        "sem lista distribuível nesta execução" if distribuivel is None
        else distribuivel.motivo_da_supressao()
    )
    lista_ok = lista_motivo is None
    if not vitrine["available"] and not lista_ok:
        return {
            "available": False,
            "reason": vitrine["reason"],
            "baseline_id": baseline_id,
            "baseline_label": label,
        }

    baseline = vitrine["snapshot"]
    baseline_listing = vitrine["listing"]
    primaria = "vitrine" if vitrine["available"] else "distribuivel"
    baseline_warning = vitrine.get("warning")
    if primaria == "distribuivel":
        baseline_warning = (
            f"{vitrine['reason']}; gaps medidos contra a lista distribuível"
        )

    competitors: list[dict] = []
    competitor_snaps: list[ShelfSnapshot] = []
    excluded: list[dict] = []
    for snapshot in snapshots:
        if snapshot.platform == baseline_id:
            continue
        comp_label = _platform_label(snapshot.platform)
        if snapshot.status == SnapshotStatus.UNAVAILABLE or not snapshot.funds:
            excluded.append(
                {
                    "platform_id": snapshot.platform,
                    "label": comp_label,
                    "reason": "coleta indisponível nesta execução",
                }
            )
            continue
        listing = _listing_funds(snapshot)
        if not listing:
            excluded.append(
                {
                    "platform_id": snapshot.platform,
                    "label": comp_label,
                    "reason": (
                        "sem vitrine pública coletada; o catálogo "
                        "Open Finance não conta como oferta"
                    ),
                }
            )
            continue
        competitor_snaps.append(snapshot)
        competitors.append(
            {
                "platform_id": snapshot.platform,
                "label": comp_label,
                "listing_total": len(listing),
                "partial": snapshot.status == SnapshotStatus.PARTIAL,
            }
        )

    vitrine_cnpjs = {f.cnpj for f in baseline_listing if f.cnpj}
    lista_cnpjs = set(distribuivel.cnpjs()) if lista_ok else set()
    base_cnpjs = vitrine_cnpjs if primaria == "vitrine" else lista_cnpjs

    # Indices da vitrine baseline para as colunas heuristicas (D3). Sao sinais
    # informativos - nunca removem um fundo da lista de gaps.
    baseline_managers = {
        normalize_text(m)
        for m in (_offer_manager(f) for f in baseline_listing)
        if m
    }
    baseline_slugs: dict[str, str] = {}
    for fund in baseline_listing:
        slug = name_slug(fund.name)
        if slug:
            baseline_slugs.setdefault(slug, fund.name)

    funds = _gap_funds(
        competitor_snaps, base_cnpjs, baseline_managers, baseline_slugs
    )

    base_primaria = familias.preparar_base(base_cnpjs) if familias else None
    base_lista = (
        familias.preparar_base(lista_cnpjs) if familias and lista_ok else None
    )
    for fund in funds:
        veredito = _veredito_contra(fund["cnpj"], base_cnpjs, familias, base_primaria)
        fund["veredito"] = veredito
        fund["veredito_distribuivel"] = None
        fund["situacao_distribuivel"] = None
        if primaria == "vitrine" and lista_ok:
            na_lista = _veredito_contra(fund["cnpj"], lista_cnpjs, familias, base_lista)
            fund["veredito_distribuivel"] = na_lista
            if na_lista and na_lista.get("coberto"):
                fund["situacao_distribuivel"] = distribuivel.situacao_de(
                    na_lista.get("cobre")
                )
        elif primaria == "distribuivel" and veredito and veredito.get("coberto"):
            fund["situacao_distribuivel"] = distribuivel.situacao_de(veredito.get("cobre"))
        if not baseline_listing and base_primaria is None:
            # Sem vitrine e sem mapa nao ha de onde saber que gestoras a XP
            # distribui: "nao" seria afirmar o que ninguem checou.
            fund["manager_on_xp"] = None
        if veredito and veredito.get("tipo") != PRESENTE:
            # Com o mapa, "gestora" e a da ESTRATEGIA (a do master), e e ela
            # que decide se a inclusao e operacional: um feeder de marca
            # propria do concorrente sobre master de terceiro e da gestora do
            # master.
            if veredito.get("gestora"):
                fund["gestora_estrategia"] = veredito["gestora"]
            if veredito.get("raiz_gestora") and base_primaria is not None:
                fund["manager_on_xp"] = veredito["raiz_gestora"] in base_primaria.raizes
            fund["familia"] = (
                {"id": veredito["familia_id"], "nome": veredito.get("familia_nome")}
                if veredito.get("familia_id") else None
            )
            fund["dmais"] = veredito.get("dmais")

    cobertos = [f for f in funds if (f["veredito"] or {}).get("coberto")]
    acao = [f for f in funds if not (f["veredito"] or {}).get("coberto")]
    reais = [f for f in acao if (f["veredito"] or {}).get("tipo") != OUTRA_LIQUIDEZ]
    categories = _gap_categories(
        baseline_listing, competitor_snaps, reais,
        cobertos=Counter(f["category"] for f in cobertos),
    )
    funil = _funil([f["veredito"] for f in funds])

    # A outra lente, sobre o mesmo universo: quanto da vitrine concorrente a
    # lista distribuivel cobre. So o funil - a lista nominal e a da primaria.
    lentes = {
        "vitrine": {
            "available": vitrine["available"], "reason": vitrine["reason"],
            "base_total": len(baseline_listing),
            "funil": funil if primaria == "vitrine" else None,
        },
        "distribuivel": {
            "available": lista_ok, "reason": lista_motivo,
            "base_total": len(lista_cnpjs),
            "funil": funil if primaria == "distribuivel" else None,
        },
    }
    if primaria == "vitrine" and lista_ok:
        universo = sorted({
            f.cnpj for s in competitor_snaps for f in _listing_funds(s) if f.cnpj
        })
        lentes["distribuivel"]["funil"] = _funil([
            _veredito_contra(c, lista_cnpjs, familias, base_lista)
            for c in universo if c not in lista_cnpjs
        ])

    return {
        "available": True,
        "reason": None,
        "baseline_id": baseline_id,
        "baseline_label": label,
        "baseline_status": (
            baseline.status.value if baseline else SnapshotStatus.UNAVAILABLE.value
        ),
        "baseline_warning": baseline_warning,
        "baseline_listing_total": len(baseline_listing),
        "base": primaria,
        "competitors": competitors,
        "excluded": excluded,
        # Manchete: gaps REAIS. Sem o mapa de familias sao os aparentes, como
        # antes; com ele, o funil diz quantos a carteira resolveu.
        "total_gaps": len(reais),
        "gaps_aparentes": len(funds),
        "gaps_liquidez": len(acao) - len(reais),
        "total_cobertos": len(cobertos),
        "multi_house_gaps": sum(1 for f in reais if f["houses_count"] >= 2),
        "funds": acao,
        "cobertos": cobertos,
        "categories": categories,
        "funil": funil,
        "lentes": lentes,
        "carteira": {
            "available": familias is not None,
            "competencias": list(familias.competencias) if familias else [],
            "com_carteira": (
                sum(1 for f in funds if familias.tem_carteira(f["cnpj"]))
                if familias else 0
            ),
            "aviso": None if familias else (
                "sem a carteira mensal da CVM (CDA): espelho e equivalência não "
                "foram verificados - gap medido CNPJ contra CNPJ"
            ),
            "avisos": list(familias.avisos) if familias else [],
        },
        "distribuivel": distribuivel.to_dict() if distribuivel is not None else None,
    }


def summarize_platform(
    snapshot: ShelfSnapshot, exclusive_cnpjs: set[str]
) -> PlatformSummary:
    """A linha comparativa da casa, medida na vitrine e so nela.

    O catalogo continua contado (`catalog_funds`) para quem quiser a casa como
    administradora, mas nenhuma metrica descritiva sai dele: comparar a vitrine
    de uma casa com o catalogo de outra e o que fazia o BTG aparecer com 4x as
    gestoras da XP.
    """
    label = _platform_label(snapshot.platform)

    # A analise principal ve so o varejo; os restritos vao ao bloco auxiliar.
    catalog: list[FundOffer] = retail_funds(snapshot)
    funds: list[FundOffer] = [f for f in catalog if f.on_listing]

    reason = None
    if not funds:
        reason = (
            f"sem vitrine pública coletada (estratégia: {snapshot.strategy.value})"
            if catalog
            else "sem fundos de varejo nesta coleta"
        )
    by_class: dict[str, int] = {}
    for asset_class in AssetClass:
        count = sum(1 for f in funds if f.asset_class == asset_class)
        if count:
            by_class[ASSET_CLASS_LABELS[asset_class]] = count

    managers: dict[str, int] = {}
    for fund in funds:
        if fund.manager_name:
            managers[fund.manager_name.strip()] = (
                managers.get(fund.manager_name.strip(), 0) + 1
            )

    # Faixas e menor minimo so sobre o minimo que a vitrine mostra: o
    # `min_initial` de um fundo que tambem esta no catalogo e o institucional do
    # Open Finance (FundOffer.minimo_vitrine). Sem isso as faixas da XP sairam
    # 100% acima de R$ 5 mil com a lista dela mostrando 80% abaixo.
    minimums = [m for f in funds if (m := f.minimo_vitrine) is not None]
    bands: dict[str, int] = {}
    for minimo in minimums:
        band = band_for(minimo)
        if band:
            bands[band] = bands.get(band, 0) + 1

    fees = [f.admin_fee_pct for f in funds if f.admin_fee_pct is not None]
    with_cnpj = sum(1 for f in funds if f.cnpj)

    return PlatformSummary(
        platform_id=snapshot.platform,
        label=label,
        status=snapshot.status.value,
        strategy=snapshot.strategy.value,
        error=snapshot.error,
        listing_funds=len(funds),
        catalog_funds=len(catalog),
        collected_funds=len(snapshot.funds),
        listing_reason=reason,
        by_class=by_class,
        distinct_managers=len(managers),
        top_managers=sorted(managers.items(), key=lambda kv: kv[1], reverse=True)[:10],
        min_bands=bands,
        lowest_min=min(minimums) if minimums else None,
        median_admin_fee=_median(fees),
        exclusive_count=len(exclusive_cnpjs),
        cnpj_coverage=(with_cnpj / len(funds)) if funds else 0.0,
    )


def build_restricted_block(report: RunReport) -> dict:
    """O relatorio auxiliar: quem ficou fora da analise de varejo, e por que."""
    platforms: list[dict] = []
    total = 0
    for snapshot in report.snapshots:
        _, restricted = split_by_profile(snapshot.funds)
        if not snapshot.funds:
            continue
        by_reason: Counter = Counter(reason for _, reason in restricted)
        total += len(restricted)
        platforms.append(
            {
                "platform_id": snapshot.platform,
                "label": _platform_label(snapshot.platform),
                "total_snapshot": len(snapshot.funds),
                "restricted": len(restricted),
                "by_reason": dict(by_reason),
                "on_listing": sum(1 for f, _ in restricted if f.on_listing),
                "sample": [
                    {"name": fund.name, "cnpj": fund.cnpj, "reason": reason}
                    for fund, reason in restricted[:8]
                ],
            }
        )
    return {"total": total, "platforms": platforms}


# Quantos fundos de cada recorte de captacao vao para o HTML. O dashboard e
# navegacao visual; a lista inteira mora na planilha.
_FLOW_EMBEDDED = 8


def _bloco(payload: dict | None) -> dict:
    """Bloco opcional pronto para o JSON do dashboard.

    Os campos com prefixo `_` carregam os objetos de dominio, que existem para
    os renderers em Python e nao serializam - e o JSON do dashboard vai inteiro
    para dentro do HTML.
    """
    if not payload or not payload.get("available"):
        return {"available": False, "reason": (payload or {}).get("reason")}
    return {k: v for k, v in payload.items() if not k.startswith("_")}


def _flow_payload(report: RunReport) -> dict:
    """Recorte de captacao que o dashboard desenha."""
    flow = report.retail_flow
    if not (flow and flow.get("available")):
        # O bloco indisponivel chega aqui COM o motivo: `main` o anexa em vez de
        # deixar None, justamente para o motivo sobreviver ate a superficie.
        return {"available": False, "reason": (flow or {}).get("reason")}

    # Import tardio: retail_flow importa deste modulo.
    from shelf_monitor.report.retail_flow import leitura_da_captacao

    grade = report.grade_opportunities or {}
    baseline_id = flow.get("baseline_id") or "xp"
    return {
        "available": True,
        # As frases de abertura, as mesmas do digest e do console.
        "leitura": leitura_da_captacao(flow, report.suprimidos),
        "baseline_id": baseline_id,
        "baseline_label": _platform_label(baseline_id),
        "com_cotistas": flow.get("com_cotistas", True),
        # Rotulo de cada casa, para as listas nao mostrarem o id ("xp, btg").
        "rotulos": {pid: _platform_label(pid) for pid in PLATFORMS},
        "por_categoria": flow.get("por_categoria") or [],
        "tendencia_por_casa": flow.get("tendencia_por_casa") or {},
        "baseline_perdendo": (flow.get("baseline_perdendo") or [])[:_FLOW_EMBEDDED],
        "periodo": flow["periodo"],
        "competencias": flow.get("competencias") or [],
        "casas": flow["casas"],
        "serie_por_casa": flow.get("serie_por_casa") or {},
        "tendencias": flow.get("tendencias") or {},
        "tendencia_labels": flow.get("tendencia_labels") or {},
        "total_estruturais": flow.get("total_estruturais", 0),
        "top_captacao": (flow.get("top_captacao") or [])[:_FLOW_EMBEDDED],
        "top_resgate": (flow.get("top_resgate") or [])[:_FLOW_EMBEDDED],
        "acelerando": (flow.get("acelerando") or [])[:_FLOW_EMBEDDED],
        "virou_saida": (flow.get("virou_saida") or [])[:_FLOW_EMBEDDED],
        "excluded": flow.get("excluded") or [],
        "grade": {
            "available": bool(grade.get("available")),
            "total": grade.get("total", 0),
            "total_transferiveis": grade.get("total_transferiveis", 0),
            "total_produto_do_concorrente": grade.get("total_produto_do_concorrente", 0),
            "total_gestora_desconhecida": grade.get("total_gestora_desconhecida", 0),
            "total_operacionais": grade.get("total_operacionais", 0),
            "total_comerciais": grade.get("total_comerciais", 0),
            "captacao_transferivel": grade.get("captacao_transferivel", 0.0),
            "cotistas_transferivel": grade.get("cotistas_transferivel", 0),
            "mais_varejo": (grade.get("mais_varejo") or [])[:_FLOW_EMBEDDED],
            "na_vitrine_perdendo": (grade.get("na_vitrine_perdendo") or [])[:_FLOW_EMBEDDED],
        },
    }


# Esforco de fechar um gap, do mais barato ao mais caro. A ordem e a da lista:
# o que a lista distribuivel ja cobre so precisa aparecer na vitrine (ou ter a
# distribuicao ativada); gap de gestora que a XP ja distribui e uma inclusao
# operacional; estrategia que a XP ja tem em outra faixa de liquidez custa o
# mesmo (a gestora ja e distribuida) mas vale menos - a estrategia ja esta na
# prateleira -, entao vem logo depois; um de gestora nova exige abrir
# relacionamento; produto proprio do concorrente nao se transfere. Medido em
# 26/09/2026 com a versao de liquidez antes da inclusao operacional: o digest
# abria com dez fundos que nao eram gap real.
ESFORCO_EXPOR = "expor na vitrine"
ESFORCO_ATIVAR = "ativar distribuição"
ESFORCO_LIQUIDEZ = "versão de liquidez"
ESFORCO_OPERACIONAL = "inclusão operacional"
ESFORCO_ABRIR_GESTORA = "abrir gestora"
ESFORCO_NAO_TRANSFERIVEL = "produto próprio do concorrente"
ESFORCO_INDEFINIDO = "gestora não identificada"

_ORDEM_ESFORCO = {
    ESFORCO_EXPOR: 0,
    ESFORCO_ATIVAR: 1,
    ESFORCO_OPERACIONAL: 2,
    ESFORCO_LIQUIDEZ: 3,
    ESFORCO_ABRIR_GESTORA: 4,
    ESFORCO_INDEFINIDO: 5,
    ESFORCO_NAO_TRANSFERIVEL: 6,
}


def _esforco_do_gap(fundo: dict) -> str:
    """Quanto custa fechar este gap.

    Os dois primeiros degraus vem das lentes: a lista distribuivel cobrindo um
    gap da vitrine, e a estrategia coberta so em outra liquidez. Dai para
    baixo decide a gestora - a da ESTRATEGIA (do master) quando o mapa de
    familias existe, a do veiculo quando nao.

    A gestora e testada contra a watchlist das casas que EXIBEM o fundo - a
    mesma definicao de "produto da casa" que o resto do monitor usa. Derivar
    isto do bloco de captacao nao funcionava: um fundo que aparece em
    `top_captacao` (e nao em `grade_opportunities`) nao carrega o campo, e o
    BTGP Tesouro Selic - produto do proprio BTG - saia rotulado "inclusao
    operacional", que e o oposto do que ele e. E testar a gestora do veiculo
    errava no sentido inverso: o BTGP Thunderbolt, feeder de marca propria do
    BTG, investe no SPX Raptor - a estrategia e da SPX.
    """
    na_lista = fundo.get("veredito_distribuivel") or {}
    if na_lista.get("coberto"):
        if fundo.get("situacao_distribuivel") == PODE_DISTRIBUIR:
            return ESFORCO_ATIVAR
        return ESFORCO_EXPOR
    if (fundo.get("veredito") or {}).get("tipo") == OUTRA_LIQUIDEZ:
        return ESFORCO_LIQUIDEZ

    gestora = fundo.get("gestora_estrategia") or fundo.get("manager")
    if not gestora:
        return ESFORCO_INDEFINIDO

    normalizada = normalize_text(gestora)
    for platform_id in fundo.get("houses") or []:
        padroes = _own_product_patterns(platform_id)
        if padroes and any(p.search(normalizada) for p in padroes):
            return ESFORCO_NAO_TRANSFERIVEL

    if fundo.get("manager_on_xp"):
        return ESFORCO_OPERACIONAL
    return ESFORCO_ABRIR_GESTORA


def ranquear_gaps(
    gap_funds: list[dict],
    retail_flow: dict | None = None,
    grade: dict | None = None,
) -> list[dict]:
    """Funde a lista de gaps com a captacao, e ordena por quanto ela vale.

    Ate aqui o monitor publicava TRES listas de "fundo que a XP nao tem", com
    universos diferentes e sem hierarquia entre elas: "Gaps XP" (618 fundos,
    ordenada por numero de casas e PL), "Oportunidade de grade" (97 -> 60,
    ordenada por captacao) e "Mais varejo". Quem abria o relatorio nao tinha
    como saber por qual comecar, e os mesmos fundos apareciam nas tres.

    Passa a ser uma lista so. O gap continua sendo o universo - e a pergunta
    completa, "o que o cliente da XP nao ve" - e a captacao entra como a
    medida de quanto cada gap vale, junto do esforco de fechar. Fundo que
    capta ordena na frente; entre os sem informe, vale o criterio antigo
    (mais casas vitrinando, depois PL).

    Nao remove nada: um gap sem informe diario continua na lista, dizendo que
    nao tem serie. O monitor informa e nao filtra.

    A captacao vem da lista INTEIRA de fundos do bloco (`fundos`). Ate
    14/10/2026 ela vinha so dos rankings (15 maiores captacoes e resgates) e da
    grade, e a maioria dos gaps saia com "-" mesmo tendo informe.
    """
    if not gap_funds:
        return gap_funds

    fluxo, oportunidades = retail_flow or {}, grade or {}
    fluxo_por_cnpj: dict[str, dict] = {}
    for bloco in (fluxo.get("fundos") or [],
                  fluxo.get("top_captacao") or [],
                  fluxo.get("top_resgate") or [],
                  oportunidades.get("mais_varejo") or [],
                  oportunidades.get("operacionais") or [],
                  oportunidades.get("comerciais") or [],
                  oportunidades.get("produto_do_concorrente") or []):
        for fundo in bloco:
            if fundo.get("cnpj"):
                fluxo_por_cnpj.setdefault(fundo["cnpj"], fundo)

    enriquecidos = []
    for fundo in gap_funds:
        fluxo = fluxo_por_cnpj.get(fundo.get("cnpj")) or {}
        esforco = _esforco_do_gap(fundo)
        enriquecidos.append({
            **fundo,
            "captacao_liquida": fluxo.get("captacao_liquida"),
            "variacao_cotistas": fluxo.get("variacao_cotistas"),
            "ticket_por_cotista": fluxo.get("ticket_por_cotista"),
            "captacao_semana": fluxo.get("captacao_semana"),
            "ritmo_semanal": fluxo.get("ritmo_semanal"),
            "esforco": esforco,
        })
    return sorted(enriquecidos, key=chave_do_gap)


def chave_do_gap(f: dict) -> tuple:
    """A ordem da lista de gaps: esforco, depois captacao em 3 meses.

    Esforco ANTES de captacao: a lista existe para decidir o que incluir,
    entao o topo tem de ser o que da para incluir. Ordenada so por captacao,
    ela abria com sete produtos do proprio BTG - a maior captacao do periodo e
    tambem o que a XP nao pode distribuir - e o leitor tinha de varrer a lista
    atras do primeiro item acionavel. Dentro de cada faixa de esforco vale a
    captacao, que e quanto o gap esta custando agora.
    """
    captacao = f.get("captacao_liquida")
    return (
        _ORDEM_ESFORCO.get(f["esforco"], 9),
        0 if captacao is not None else 1,
        -(captacao or 0.0),
        -(f.get("houses_count") or 0),
        -(f.get("pl_cvm") or 0.0),
    )


def _diff_summary(diff: ShelfDiff, report: RunReport) -> dict:
    casa = movimentos_de_vitrine(diff, report.snapshot_for(diff.platform))
    return {
        "platform": diff.platform,
        "added": len(casa.entradas),
        "removed": len(casa.saidas),
        "changed": len(casa.alteracoes),
        "so_catalogo": casa.so_catalogo,
        "suspect": diff.suspect,
        "is_first_run": diff.is_first_run,
        "nova_linha_de_base": diff.nova_linha_de_base,
    }


def build_dashboard_data(report: RunReport) -> dict:
    """Estrutura compacta consumida pelo dashboard e pelo digest."""
    exclusive = _exclusive_cnpjs(report.snapshots)

    summaries = [
        summarize_platform(snapshot, exclusive.get(snapshot.platform, set()))
        for snapshot in report.snapshots
    ]
    # XP primeiro: e o baseline contra o qual as demais sao lidas.
    summaries.sort(key=lambda s: (s.platform_id != "xp", -s.listing_funds))

    cvm_payload: dict = {"available": False}
    if report.cvm_snapshot:
        snapshot = report.cvm_snapshot
        by_house = snapshot.by_house()
        cvm_payload = {
            "available": snapshot.status != SnapshotStatus.UNAVAILABLE,
            "status": snapshot.status.value,
            "error": snapshot.error,
            "collected_at": snapshot.collected_at.isoformat(),
            "total": len(snapshot.funds),
            "houses": [
                {
                    "house": house,
                    "label": _platform_label(house),
                    "total": len(funds),
                    "pre_operational": sum(1 for f in funds if f.is_pre_operational),
                }
                for house, funds in sorted(by_house.items())
            ],
            "pre_operational_funds": [
                {
                    "cnpj": f.cnpj,
                    "name": f.name,
                    "house": f.house,
                    "house_label": _platform_label(f.house),
                    "manager": f.manager_name,
                    "registered_at": f.registered_at,
                    "situation": f.situation,
                }
                for f in sorted(
                    (f for f in snapshot.funds if f.is_pre_operational),
                    key=lambda f: f.registered_at or "",
                    reverse=True,
                )[:60]
            ],
        }

    if report.cvm_diff:
        cvm_payload["diff"] = {
            "is_first_run": report.cvm_diff.is_first_run,
            "new_registrations": len(report.cvm_diff.new_registrations),
            "launches": len(report.cvm_diff.launches),
            "cancelled": len(report.cvm_diff.cancelled),
        }

    class_order = [
        ASSET_CLASS_LABELS[c]
        for c in AssetClass
        if any(ASSET_CLASS_LABELS[c] in s.by_class for s in summaries)
    ]

    # A lista completa de gaps vai a planilha (que chama build_gap_block
    # direto); o HTML embute so o topo para o payload nao inchar.
    gaps = bloco_de_gaps(report)
    if not gaps.get("nao_se_aplica"):
        gap_funds = ranquear_gaps(
            gaps.get("funds") or [], report.retail_flow, report.grade_opportunities
        )
        cobertos = gaps.get("cobertos") or []
        gaps = {
            **gaps,
            "funds": gap_funds[:_GAP_FUNDS_EMBEDDED],
            "funds_truncated": max(0, len(gap_funds) - _GAP_FUNDS_EMBEDDED),
            "cobertos": cobertos[:_GAP_FUNDS_EMBEDDED],
            "cobertos_truncated": max(0, len(cobertos) - _GAP_FUNDS_EMBEDDED),
            # As frases saem prontas: o JS desenha, nao redige - e assim o
            # painel diz exatamente o que o digest e o deck dizem.
            "frase_funil": frase_do_funil(gaps),
            "notas_cobertura": notas_da_cobertura(gaps),
        }

    return {
        "generated_at": report.generated_at.isoformat(),
        # Blocos que o modulo declarou sem sentido (chave -> motivo): o
        # dashboard e o deck trocam cada um pelo motivo.
        "suprimidos": dict(report.suprimidos),
        "listing": build_listing_block(report),
        "gaps": gaps,
        "restricted": build_restricted_block(report),
        # Captacao entra ja calculada pelo build_report: o dashboard nao le o
        # informe diario da CVM por conta propria - o payload e uma foto.
        "flow": _flow_payload(report),
        # Rentabilidade ponderada e custo relativo entram ja calculados, como a
        # captacao: o dashboard desenha uma foto, nao le o informe nem o
        # extrato por conta propria.
        "performance": _bloco(report.performance),
        "performance_12m": _bloco(report.performance_12m),
        "custo": _bloco(report.custo),
        "mandato": _bloco(report.mandato),
        "enrichment": report.enrichment,
        "class_order": class_order,
        "band_order": [label for label, _, _ in MIN_BANDS],
        # Ordem canonica das casas: fixa a cor de cada uma nos graficos, mesmo
        # quando a execucao cobre so parte delas (o radar CVM traz casas que
        # nao tem prateleira coletada).
        "house_order": list(PLATFORMS),
        "platforms": [s.to_dict() for s in summaries],
        # O veredito do bloco de aplicacao minima, calculado aqui para as
        # quatro superficies dizerem a mesma coisa.
        "leitura_ticket": leitura_do_ticket([s.to_dict() for s in summaries]),
        # O resumo por plataforma ja carrega o que o dashboard desenha; os
        # diffs completos ficam no digest, para nao embutir a prateleira
        # inteira duas vezes no HTML. As contagens sao de vitrine, a mesma
        # regra do digest, do assunto e do deck; o resto vai em `so_catalogo`.
        "diff_summary": [_diff_summary(d, report) for d in report.shelf_diffs],
        "cvm": cvm_payload,
    }
