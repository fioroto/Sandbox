"""Agregacoes competitivas derivadas dos snapshots - insumo dos relatorios."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from shelf_monitor.config import PLATFORMS, WATCHLIST
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM, KEY_GESTOR_OFICIAL
from shelf_monitor.normalize import format_cnpj, normalize_text
from shelf_monitor.profile import restriction_reason, split_by_profile
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
    """Uma linha da matriz competitiva de prateleira."""

    platform_id: str
    label: str
    status: str
    strategy: str
    error: str | None
    total_funds: int
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
            "total_funds": self.total_funds,
            "by_class": self.by_class,
            "distinct_managers": self.distinct_managers,
            "top_managers": [{"name": n, "count": c} for n, c in self.top_managers],
            "min_bands": self.min_bands,
            "lowest_min": self.lowest_min,
            "median_admin_fee": self.median_admin_fee,
            "exclusive_count": self.exclusive_count,
            "cnpj_coverage": round(self.cnpj_coverage, 3),
        }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _quartiles(values: list[float]) -> dict | None:
    """q1/mediana/q3 - quartis dizem a forma da prateleira; media esconde."""
    if len(values) < 4:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    lower = ordered[:mid]
    upper = ordered[mid + 1 :] if len(ordered) % 2 else ordered[mid:]
    return {
        "q1": round(_median(lower), 4),
        "median": round(_median(ordered), 4),
        "q3": round(_median(upper), 4),
        "n": len(ordered),
    }


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


def anbima_category(fund: FundOffer) -> str:
    """Categoria do fundo, na melhor fonte disponivel, em tres camadas.

    1. Classificacao ANBIMA oficial - so existe para fundo aderente a ANBIMA;
    2. Classificacao CVM oficial (Renda Fixa/Acoes/Multimercado/Cambial) - o
       registro sempre a tem, entao um fundo casado nunca fica "sem classe";
    3. a heuristica sobre nome/categoria da casa, quando nem o join houve.
    Rotulos ficam como estao (a granularidade diferente e visivel por si), e o
    "Sem classificacao" sobra so para quem nao tem CNPJ casado nem nome legivel.
    """
    official = fund.extra.get(KEY_ANBIMA) or fund.extra.get(KEY_CVM)
    if official:
        return official
    if fund.asset_class != AssetClass.OUTROS:
        return ASSET_CLASS_LABELS[fund.asset_class]
    return "Sem classificacao"


def _listing_funds(snapshot: ShelfSnapshot) -> list[FundOffer]:
    return [f for f in retail_funds(snapshot) if f.on_listing]


# Quantas categorias ANBIMA aparecem por extenso na matriz; o resto agrega em
# "Outras" para a tabela caber numa tela de comite.
_MATRIX_CATEGORIES = 12


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

    presence: dict[str, set[str]] = {}
    platforms: list[dict] = []
    category_totals: Counter = Counter()

    for snapshot in usable:
        listing = _listing_funds(snapshot)
        for fund in listing:
            if fund.cnpj:
                presence.setdefault(fund.cnpj, set()).add(snapshot.platform)

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
    """CNPJs de varejo presentes em exatamente uma plataforma coletada.

    E o conceito COMPETITIVO ("so esta casa oferta"), nao o regulatorio
    ("fundo exclusivo de um investidor") - os regulatorios nem entram aqui.
    So considera snapshots utilizaveis: uma coleta que falhou tornaria
    diferencial todo fundo das demais casas.
    """
    usable = [s for s in snapshots if s.status != SnapshotStatus.UNAVAILABLE and s.funds]
    presence: dict[str, set[str]] = {}
    for snapshot in usable:
        for fund in retail_funds(snapshot):
            if fund.cnpj:
                presence.setdefault(fund.cnpj, set()).add(snapshot.platform)

    exclusive: dict[str, set[str]] = {s.platform: set() for s in usable}
    for cnpj, platforms in presence.items():
        if len(platforms) == 1:
            exclusive[next(iter(platforms))].add(cnpj)
    return exclusive


def summarize_platform(
    snapshot: ShelfSnapshot, exclusive_cnpjs: set[str]
) -> PlatformSummary:
    platform_cfg = PLATFORMS.get(snapshot.platform)
    label = platform_cfg.label if platform_cfg else snapshot.platform

    # A analise principal ve so o varejo; os restritos vao ao bloco auxiliar.
    funds: list[FundOffer] = retail_funds(snapshot)
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
        total_funds=len(funds),
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


# --- Sobreposicao competitiva e arbitragem de preco --------------------------

# Abaixo desta mediana a casa publica a taxa como fracao (0.015 = 1,5% a.a.):
# prateleira de varejo inteira com mediana < 0,2% a.a. nao existe no Brasil.
_FEE_FRACTION_MEDIAN = 0.2
# Divergencia minima (em p.p., pos-canonicalizacao) para contar como arbitragem;
# abaixo disso e arredondamento de fonte, nao decisao de preco.
_FEE_SPREAD_MIN_PP = 0.10
# Mesmo fundo com razao max/min de taxa acima disto e erro de unidade no dado.
_FEE_UNIT_SUSPECT_RATIO = 20.0
# Diferenca de minimo que de fato muda o acesso de um investidor de varejo.
_MIN_DIFF_FLOOR = 1_000.0
_ARBITRAGE_TOP = 15
_CORE_TOP = 60


def _usable_snapshots(snapshots: list[ShelfSnapshot]) -> list[ShelfSnapshot]:
    return [s for s in snapshots if s.status != SnapshotStatus.UNAVAILABLE and s.funds]


def _platform_label(platform_id: str) -> str:
    cfg = PLATFORMS.get(platform_id)
    return cfg.label if cfg else platform_id


def _fee_scale(values: list[float]) -> float:
    """Multiplicador que canonicaliza a taxa da casa para percentual ao ano."""
    med = _median(values)
    if med is None:
        return 1.0
    return 100.0 if med < _FEE_FRACTION_MEDIAN else 1.0


def overlap_block_from_snapshots(snapshots: list[ShelfSnapshot]) -> dict:
    """Sobreposicao par-a-par, nucleo comum e arbitragem de preco por CNPJ.

    Compara so o varejo (a materia-prima de toda analise principal) e so casas
    com coleta utilizavel - uma coleta que falhou tornaria "exclusivo" todo
    fundo das demais. A taxa e canonicalizada para percentual por casa antes de
    comparar: sem isso a diferenca de unidade entre fontes viraria arbitragem.
    """
    usable = _usable_snapshots(snapshots)
    ordered = [s for pid in PLATFORMS for s in usable if s.platform == pid]
    ordered += [s for s in usable if s.platform not in PLATFORMS]
    platform_ids = [s.platform for s in ordered]
    labels = {pid: _platform_label(pid) for pid in platform_ids}

    retail_by_platform = {s.platform: retail_funds(s) for s in ordered}
    fee_scale = {
        pid: _fee_scale([f.admin_fee_pct for f in funds if f.admin_fee_pct is not None])
        for pid, funds in retail_by_platform.items()
    }

    presence: dict[str, set[str]] = {}
    fund_by_key: dict[tuple[str, str], FundOffer] = {}
    for pid, funds in retail_by_platform.items():
        for fund in funds:
            if not fund.cnpj:
                continue
            presence.setdefault(fund.cnpj, set()).add(pid)
            fund_by_key.setdefault((pid, fund.cnpj), fund)

    pair_matrix = [
        {
            "a": a,
            "b": b,
            "shared": sum(1 for plats in presence.values() if a in plats and b in plats),
        }
        for a, b in combinations(platform_ids, 2)
    ]
    diagonal = {
        pid: len({f.cnpj for f in funds if f.cnpj})
        for pid, funds in retail_by_platform.items()
    }
    multi_home = Counter(len(p) for p in presence.values() if len(p) >= 2)

    core_funds: list[dict] = []
    if len(platform_ids) >= 2:
        everywhere = [
            cnpj for cnpj, plats in presence.items() if len(plats) == len(platform_ids)
        ]
        for cnpj in everywhere:
            fund = fund_by_key[(platform_ids[0], cnpj)]
            core_funds.append(
                {
                    "cnpj": format_cnpj(cnpj),
                    "name": fund.name,
                    "category": anbima_category(fund),
                }
            )
        core_funds.sort(key=lambda f: f["name"])

    fee_universe = fee_significant = unit_suspect = 0
    fee_items: list[dict] = []
    min_universe = min_significant = 0
    min_items: list[dict] = []

    for cnpj, plats in presence.items():
        if len(plats) < 2:
            continue

        fees = {}
        mins = {}
        for pid in plats:
            fund = fund_by_key[(pid, cnpj)]
            if fund.admin_fee_pct is not None:
                fees[pid] = round(fund.admin_fee_pct * fee_scale[pid], 4)
            if fund.min_initial is not None:
                mins[pid] = fund.min_initial

        if len(fees) >= 2:
            fee_universe += 1
            low, high = min(fees.values()), max(fees.values())
            spread = round(high - low, 2)
            if low > 0 and high / low >= _FEE_UNIT_SUSPECT_RATIO:
                unit_suspect += 1
            elif spread >= _FEE_SPREAD_MIN_PP:
                fee_significant += 1
                cheapest = min(fees, key=fees.get)
                fee_items.append(
                    {
                        "cnpj": format_cnpj(cnpj),
                        "name": fund_by_key[(cheapest, cnpj)].name,
                        "spread_pp": spread,
                        "fees": {pid: round(v, 2) for pid, v in fees.items()},
                        "cheapest": cheapest,
                        "priciest": max(fees, key=fees.get),
                    }
                )

        if len(mins) >= 2:
            min_universe += 1
            low, high = min(mins.values()), max(mins.values())
            diff = high - low
            if diff >= _MIN_DIFF_FLOOR:
                min_significant += 1
                lowest = min(mins, key=mins.get)
                min_items.append(
                    {
                        "cnpj": format_cnpj(cnpj),
                        "name": fund_by_key[(lowest, cnpj)].name,
                        "diff": diff,
                        "mins": dict(mins),
                        "lowest": lowest,
                    }
                )

    fee_items.sort(key=lambda i: i["spread_pp"], reverse=True)
    min_items.sort(key=lambda i: i["diff"], reverse=True)

    return {
        "platforms": platform_ids,
        "labels": labels,
        "pair_matrix": pair_matrix,
        "diagonal": diagonal,
        "multi_home": {str(k): v for k, v in sorted(multi_home.items())},
        "core_all": {"count": len(core_funds), "funds": core_funds[:_CORE_TOP]},
        "fee_arbitrage": {
            "universe": fee_universe,
            "significant": fee_significant,
            "unit_suspect": unit_suspect,
            "top": fee_items[:_ARBITRAGE_TOP],
        },
        "min_arbitrage": {
            "universe": min_universe,
            "significant": min_significant,
            "top": min_items[:_ARBITRAGE_TOP],
        },
    }


def build_overlap_block(report: RunReport) -> dict:
    return overlap_block_from_snapshots(report.snapshots)


def build_dashboard_data(report: RunReport) -> dict:
    """Estrutura compacta consumida pelo dashboard e pelo digest."""
    exclusive = _exclusive_cnpjs(report.snapshots)

    summaries = [
        summarize_platform(snapshot, exclusive.get(snapshot.platform, set()))
        for snapshot in report.snapshots
    ]
    # XP primeiro: e o baseline contra o qual as demais sao lidas.
    summaries.sort(key=lambda s: (s.platform_id != "xp", -s.total_funds))

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

    return {
        "generated_at": report.generated_at.isoformat(),
        "listing": build_listing_block(report),
        "overlap": build_overlap_block(report),
        "restricted": build_restricted_block(report),
        "enrichment": report.enrichment,
        "class_order": class_order,
        "band_order": [label for label, _, _ in MIN_BANDS],
        # Ordem canonica das casas: fixa a cor de cada uma nos graficos, mesmo
        # quando a execucao cobre so parte delas (o radar CVM traz casas que
        # nao tem prateleira coletada).
        "house_order": list(PLATFORMS),
        "platforms": [s.to_dict() for s in summaries],
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
