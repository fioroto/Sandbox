"""Agregacoes competitivas derivadas dos snapshots - insumo dos relatorios."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from shelf_monitor.config import PLATFORMS, WATCHLIST
from shelf_monitor.enrich import KEY_ANBIMA, KEY_CVM, KEY_GESTOR_OFICIAL, KEY_PL
from shelf_monitor.normalize import format_cnpj, name_slug, normalize_text
from shelf_monitor.profile import restriction_reason, split_by_profile
from shelf_monitor.models import (
    ASSET_CLASS_LABELS,
    AssetClass,
    FundOffer,
    RunReport,
    ShelfSnapshot,
    SnapshotStatus,
)

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


def _platform_label(platform_id: str) -> str:
    return PLATFORMS[platform_id].label if platform_id in PLATFORMS else platform_id


def _canonical_order(platform_ids: set[str]) -> list[str]:
    """Ordem canonica das casas (a mesma das cores dos graficos)."""
    ordered = [pid for pid in PLATFORMS if pid in platform_ids]
    ordered.extend(sorted(platform_ids - set(PLATFORMS)))
    return ordered


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


_CAPTACAO_LIMITE = 10
_CAPTACAO_JANELA_DIGEST = "semana"


def build_captacao_block(report: RunReport, limite: int = _CAPTACAO_LIMITE) -> dict:
    """O monitor de captacao: rankings, consolidacao por classificacao e
    analise cruzada, a partir de `report.captacao` (ver `shelf_monitor.captacao`).

    Segue a mesma convencao de supressao de `build_gap_block`: quando o
    resultado nao existe, ou existe mas nenhuma vitrine entrou no universo, o
    bloco sai com `available: False` e o motivo - nunca com numeros parciais
    sem dizer o que falta.
    """
    from shelf_monitor.captacao import (
        NOTA_FUNDO_NIVEL,
        analise_cruzada,
        consolidar_por_classificacao,
        ranking_consolidado,
        ranking_por_plataforma,
    )

    resultado = report.captacao
    if resultado is None:
        return {
            "available": False,
            "reason": (
                "captacao nao calculada nesta execucao (rode 'run "
                "--with-captacao' ou 'python -m shelf_monitor captacao')"
            ),
        }

    base = {
        "universo": resultado.universo,
        "rotulo_universo": resultado.rotulo_universo,
        "nota": NOTA_FUNDO_NIVEL,
        "gerado_em": resultado.gerado_em.isoformat(),
        "excluded": resultado.excluded,
    }

    if not resultado.plataformas:
        return {
            **base,
            "available": False,
            "reason": (
                "nenhuma vitrine entrou no universo desta execucao; o "
                "catalogo Open Finance nao conta como vitrine"
            ),
        }

    if resultado.cobertura.total_com_fluxo == 0:
        return {
            **base,
            "available": False,
            "reason": (
                "nenhum fundo da vitrine foi encontrado no INF_DIARIO da CVM "
                f"(meses lidos: {', '.join(resultado.cobertura.meses_lidos) or 'nenhum'})"
            ),
        }

    janelas_chaves = [j.chave for j in resultado.janelas]
    rankings = {
        chave: {
            "consolidado": ranking_consolidado(resultado, chave, limite),
            "por_plataforma": ranking_por_plataforma(resultado, chave, limite),
        }
        for chave in janelas_chaves
    }
    classificacao = {chave: consolidar_por_classificacao(resultado, chave) for chave in janelas_chaves}
    cruzada = {
        chave: analise_cruzada(resultado, chave=chave, limite=limite)
        for chave in janelas_chaves
        if chave == _CAPTACAO_JANELA_DIGEST or chave == "ano"
    }

    return {
        **base,
        "available": True,
        "ultima_data": (
            resultado.cobertura.ultima_data.isoformat()
            if resultado.cobertura.ultima_data
            else None
        ),
        "janelas": [j.to_dict() for j in resultado.janelas],
        "janela_padrao": _CAPTACAO_JANELA_DIGEST,
        "plataformas": [
            {"platform_id": pid, **info} for pid, info in resultado.cobertura.por_plataforma.items()
        ],
        "rankings": rankings,
        "classificacao": classificacao,
        "cruzada": cruzada,
        "cobertura": resultado.cobertura.to_dict(),
    }


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

    # A lista completa de gaps vai a planilha (que chama build_gap_block
    # direto); o HTML embute so o topo para o payload nao inchar.
    gaps = build_gap_block(report.snapshots)
    gap_funds = gaps.get("funds") or []

    return {
        "generated_at": report.generated_at.isoformat(),
        "listing": build_listing_block(report),
        "gaps": {
            **gaps,
            "funds": gap_funds[:_GAP_FUNDS_EMBEDDED],
            "funds_truncated": max(0, len(gap_funds) - _GAP_FUNDS_EMBEDDED),
        },
        "restricted": build_restricted_block(report),
        "captacao": build_captacao_block(report),
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
