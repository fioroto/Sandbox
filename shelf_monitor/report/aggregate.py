"""Agregacoes competitivas derivadas dos snapshots - insumo dos relatorios."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from shelf_monitor.classify import KEY_CATEGORIA
from shelf_monitor.config import PLATFORMS, WATCHLIST
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM, KEY_GESTOR_OFICIAL, KEY_PL
from shelf_monitor.normalize import format_cnpj, name_slug, normalize_text
from shelf_monitor.profile import split_by_profile
from shelf_monitor.report.shared import (
    mediana,
    ordem_canonica,
    quartis,
    rotulo_da_casa,
)
from shelf_monitor.models import (
    AssetClass,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)

ASSET_CLASS_LABELS: dict[AssetClass, str] = {
    AssetClass.RENDA_FIXA: "Renda Fixa",
    AssetClass.MULTIMERCADO: "Multimercado",
    AssetClass.ACOES: "Acoes",
    AssetClass.CAMBIAL: "Cambial",
    AssetClass.INTERNACIONAL: "Internacional",
    AssetClass.PREVIDENCIA: "Previdencia",
    AssetClass.OUTROS: "Outros",
}

# Faixas de aplicacao minima - separam varejo de alta renda e private.
MIN_BANDS: list[tuple[str, float, float]] = [
    ("Ate R$ 100", 0.0, 100.0),
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
    return "Sem classificacao"


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
    da prateleira de cada casa e parava ai. Mas a forma diz uma coisa so, e
    ela e acionavel - medido em 2026-09-21, a vitrine da XP nao tem NENHUM
    fundo abaixo de R$ 5 mil, contra 230 no BTG e 340 no Itau. Nao e uma
    diferenca de composicao: e a ausencia de uma porta de entrada.

    Devolve None quando nao ha o que concluir - baseline sem vitrine, ou
    nenhuma concorrente com faixa publicada.
    """
    faixas_varejo = [rotulo for rotulo, _, teto in MIN_BANDS if teto <= _TICKET_VAREJO]

    def conta_varejo(p: dict) -> int:
        bandas = p.get("min_bands") or p.get("bands") or {}
        return sum(bandas.get(rotulo, 0) for rotulo in faixas_varejo)

    com_faixa = [p for p in platforms if (p.get("min_bands") or p.get("bands"))]
    baseline = next((p for p in com_faixa if p["platform_id"] == baseline_id), None)
    if baseline is None:
        return None
    pares = [p for p in com_faixa if p["platform_id"] != baseline_id]
    if not pares:
        return None

    meu = conta_varejo(baseline)
    melhor = max(pares, key=conta_varejo)
    dele = conta_varejo(melhor)
    return {
        "baseline_id": baseline_id,
        "baseline_label": baseline["label"],
        "baseline_varejo": meu,
        "melhor_label": melhor["label"],
        "melhor_varejo": dele,
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
                    "label": PLATFORMS[snapshot.platform].label
                    if snapshot.platform in PLATFORMS
                    else snapshot.platform,
                    "total": 0,
                    "reason": "sem listagem publica alcancavel nesta coleta",
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
                "label": PLATFORMS[snapshot.platform].label
                if snapshot.platform in PLATFORMS
                else snapshot.platform,
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
                "min_quartiles": _quartiles(
                    [f.min_initial for f in listing if f.min_initial is not None]
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


def build_gap_block(
    snapshots: list[ShelfSnapshot], baseline_id: str | None = None
) -> dict:
    """Gaps de prateleira do baseline: o que as vitrines concorrentes ofertam
    de varejo e a vitrine da XP nao tem.

    A base e sempre a vitrine, dos dois lados (vitrine x vitrine): o catalogo
    Open Finance nao entra em nenhum ponto da conta - nem para desqualificar um
    gap, nem nas contagens por categoria. Um fundo que a XP so tem via Open
    Finance conta como gap, porque o cliente nao o ve na vitrine.

    Protecoes anti-falso-alarme, na mesma linha de _exclusive_cnpjs:
    - baseline ausente, indisponivel ou sem vitrine -> bloco suprimido com
      motivo (tudo pareceria gap);
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
    baseline = next((s for s in snapshots if s.platform == baseline_id), None)
    if baseline is None:
        return {
            "available": False,
            "reason": f"snapshot da {label} ausente desta execucao",
            "baseline_id": baseline_id,
            "baseline_label": label,
        }
    if baseline.status == SnapshotStatus.UNAVAILABLE or not baseline.funds:
        detail = f": {baseline.error}" if baseline.error else ""
        return {
            "available": False,
            "reason": f"coleta da {label} indisponivel{detail}",
            "baseline_id": baseline_id,
            "baseline_label": label,
        }

    baseline_listing = _listing_funds(baseline)
    if not baseline_listing:
        return {
            "available": False,
            "reason": (
                f"{label} sem vitrine coletada nesta execucao "
                f"(estrategia: {baseline.strategy.value}); o catalogo "
                "Open Finance nao conta como vitrine"
            ),
            "baseline_id": baseline_id,
            "baseline_label": label,
        }

    baseline_warning = None
    if baseline.status == SnapshotStatus.PARTIAL:
        detail = f" ({baseline.error})" if baseline.error else ""
        baseline_warning = (
            f"coleta da {label} parcial{detail}; gaps podem estar superestimados"
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
                    "reason": "coleta indisponivel nesta execucao",
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
                        "sem vitrine publica coletada; o catalogo "
                        "Open Finance nao conta como oferta"
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

    baseline_cnpjs = {f.cnpj for f in baseline_listing if f.cnpj}

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

    # Exemplares por CNPJ de gap: (casa, fundo) de cada vitrine concorrente.
    offers_by_cnpj: dict[str, list[tuple[str, FundOffer]]] = {}
    for snapshot in competitor_snaps:
        for fund in _listing_funds(snapshot):
            if fund.cnpj and fund.cnpj not in baseline_cnpjs:
                offers_by_cnpj.setdefault(fund.cnpj, []).append(
                    (snapshot.platform, fund)
                )

    funds: list[dict] = []
    gap_categories: Counter = Counter()
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
        gap_categories[category] += 1

        manager = next(
            (m for m in (_offer_manager(f) for _, f in offers) if m), None
        )
        minimums = [f.min_initial for _, f in offers if f.min_initial is not None]
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

    # Cobertura por categoria: vitrine XP vs. vitrine de cada concorrente.
    baseline_by_category: Counter = Counter(
        anbima_category(f) for f in baseline_listing
    )
    competitor_by_category: dict[str, Counter] = {
        s.platform: Counter(anbima_category(f) for f in _listing_funds(s))
        for s in competitor_snaps
    }

    top_categories = [c for c, _ in gap_categories.most_common(_GAP_CATEGORIES)]
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
            }
        )

    return {
        "available": True,
        "reason": None,
        "baseline_id": baseline_id,
        "baseline_label": label,
        "baseline_status": baseline.status.value,
        "baseline_warning": baseline_warning,
        "baseline_listing_total": len(baseline_listing),
        "competitors": competitors,
        "excluded": excluded,
        "total_gaps": len(funds),
        "multi_house_gaps": sum(1 for f in funds if f["houses_count"] >= 2),
        "funds": funds,
        "categories": categories,
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
    platform_cfg = PLATFORMS.get(snapshot.platform)
    label = platform_cfg.label if platform_cfg else snapshot.platform

    # A analise principal ve so o varejo; os restritos vao ao bloco auxiliar.
    catalog: list[FundOffer] = retail_funds(snapshot)
    funds: list[FundOffer] = [f for f in catalog if f.on_listing]

    reason = None
    if not funds:
        reason = (
            f"sem vitrine publica coletada (estrategia: {snapshot.strategy.value})"
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

    bands: dict[str, int] = {}
    for fund in funds:
        band = band_for(fund.min_initial)
        if band:
            bands[band] = bands.get(band, 0) + 1

    minimums = [f.min_initial for f in funds if f.min_initial is not None]
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
                "label": PLATFORMS[snapshot.platform].label
                if snapshot.platform in PLATFORMS
                else snapshot.platform,
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

    grade = report.grade_opportunities or {}
    return {
        "available": True,
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


# Esforco de incluir um fundo de gap na grade, do mais barato ao mais caro.
# A ordem e a da lista: um gap de gestora que a XP ja distribui e uma inclusao
# operacional; um de gestora nova exige abrir relacionamento; produto proprio
# do concorrente nao se transfere.
ESFORCO_OPERACIONAL = "inclusao operacional"
ESFORCO_ABRIR_GESTORA = "abrir gestora"
ESFORCO_NAO_TRANSFERIVEL = "produto proprio do concorrente"
ESFORCO_INDEFINIDO = "gestora nao identificada"

_ORDEM_ESFORCO = {
    ESFORCO_OPERACIONAL: 0,
    ESFORCO_ABRIR_GESTORA: 1,
    ESFORCO_INDEFINIDO: 2,
    ESFORCO_NAO_TRANSFERIVEL: 3,
}


def _esforco_do_gap(fundo: dict) -> str:
    """Quanto custa colocar este gap na grade.

    A gestora e testada contra a watchlist das casas que EXIBEM o fundo - a
    mesma definicao de "produto da casa" que o resto do monitor usa. Derivar
    isto do bloco de captacao nao funcionava: um fundo que aparece em
    `top_captacao` (e nao em `grade_opportunities`) nao carrega o campo, e o
    BTGP Tesouro Selic - produto do proprio BTG - saia rotulado "inclusao
    operacional", que e o oposto do que ele e.
    """
    gestora = fundo.get("manager")
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
    """
    if not gap_funds:
        return gap_funds

    fluxo, oportunidades = retail_flow or {}, grade or {}
    fluxo_por_cnpj: dict[str, dict] = {}
    for bloco in (fluxo.get("top_captacao") or [],
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
        fluxo = fluxo_por_cnpj.get(fundo.get("cnpj"))
        esforco = _esforco_do_gap(fundo)
        enriquecidos.append({
            **fundo,
            "captacao_liquida": (fluxo or {}).get("captacao_liquida"),
            "variacao_cotistas": (fluxo or {}).get("variacao_cotistas"),
            "esforco": esforco,
        })

    def chave(f: dict) -> tuple:
        # Esforco ANTES de captacao: a lista existe para decidir o que incluir,
        # entao o topo tem de ser o que da para incluir. Ordenada so por
        # captacao, ela abria com sete produtos do proprio BTG - a maior
        # captacao do periodo e tambem o que a XP nao pode distribuir - e o
        # leitor tinha de varrer a lista atras do primeiro item acionavel.
        # Dentro de cada faixa de esforco vale a captacao, que e quanto o gap
        # esta custando agora.
        captacao = f.get("captacao_liquida")
        return (
            _ORDEM_ESFORCO.get(f["esforco"], 9),
            0 if captacao is not None else 1,
            -(captacao or 0.0),
            -(f.get("houses_count") or 0),
            -(f.get("pl_cvm") or 0.0),
        )

    return sorted(enriquecidos, key=chave)


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
                    "label": PLATFORMS[house].label if house in PLATFORMS else house,
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
                    "house_label": (
                        PLATFORMS[f.house].label if f.house in PLATFORMS else f.house
                    ),
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
    gaps = build_gap_block(report.snapshots)
    gap_funds = ranquear_gaps(
        gaps.get("funds") or [], report.retail_flow, report.grade_opportunities
    )

    return {
        "generated_at": report.generated_at.isoformat(),
        "listing": build_listing_block(report),
        "gaps": {
            **gaps,
            "funds": gap_funds[:_GAP_FUNDS_EMBEDDED],
            "funds_truncated": max(0, len(gap_funds) - _GAP_FUNDS_EMBEDDED),
        },
        "restricted": build_restricted_block(report),
        # Captacao entra ja calculada pelo build_report: o dashboard nao le o
        # informe diario da CVM por conta propria - o payload e uma foto.
        "flow": _flow_payload(report),
        # Rentabilidade ponderada e custo relativo entram ja calculados, como a
        # captacao: o dashboard desenha uma foto, nao le o informe nem o
        # extrato por conta propria.
        "performance": _bloco(report.performance),
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
        # inteira duas vezes no HTML.
        "diff_summary": [
            {
                "platform": d.platform,
                "added": len(d.added),
                "removed": len(d.removed),
                "changed": len(d.changed),
                "suspect": d.suspect,
                "is_first_run": d.is_first_run,
            }
            for d in report.shelf_diffs
        ],
        "cvm": cvm_payload,
    }
