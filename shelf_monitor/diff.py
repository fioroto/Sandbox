"""Comparacao entre snapshots: o que entrou, saiu e mudou.

O risco central de um monitor assim e o falso churn: uma coleta parcial faz a
prateleira inteira parecer ter sumido. Por isso o diff so reporta saidas quando
confia na coleta atual (ver `SUSPECT_SHRINK_RATIO`).
"""
from __future__ import annotations

from difflib import SequenceMatcher

from shelf_monitor.config import (
    FUZZY_RENAME_THRESHOLD,
    SUSPECT_SHRINK_RATIO,
)
from shelf_monitor.models import (
    CvmDiff,
    CvmFund,
    CvmSituationChange,
    CvmSnapshot,
    FieldChange,
    FundChange,
    FundOffer,
    ShelfDiff,
    ShelfSnapshot,
    SnapshotStatus,
)
from shelf_monitor.normalize import name_slug

# (rotulo, extrator) - o rotulo aparece no digest.
_TRACKED = (
    ("min_initial", lambda f: f.min_initial),
    ("admin_fee_pct", lambda f: f.admin_fee_pct),
    (
        "performance_fee_pct",
        lambda f: f.performance_fee.amount_pct if f.performance_fee else None,
    ),
    ("closed_for_investment", lambda f: f.closed_for_investment),
    ("redemption_quotation_days", lambda f: f.redemption_quotation_days),
    ("redemption_payment_days", lambda f: f.redemption_payment_days),
    ("grace_days", lambda f: f.grace_days),
)


def _index(funds: list[FundOffer]) -> dict[str, FundOffer]:
    return {fund.identity: fund for fund in funds}


def _field_changes(old: FundOffer, new: FundOffer) -> list[FieldChange]:
    changes: list[FieldChange] = []
    for label, extract in _TRACKED:
        old_value = extract(old)
        new_value = extract(new)
        # Um campo que passou a ser desconhecido nao e uma mudanca de oferta:
        # normalmente e a fonte que parou de publicar aquele dado.
        if new_value is None or old_value is None:
            continue
        if old_value != new_value:
            changes.append(FieldChange(field=label, old=old_value, new=new_value))
    return changes


def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _reconcile_renames(
    added: list[FundOffer], removed: list[FundOffer]
) -> tuple[list[FundOffer], list[FundOffer], list[FundChange]]:
    """Casa entradas com saidas muito parecidas: e renomeacao, nao troca.

    So se aplica a fundos sem CNPJ - com CNPJ a identidade ja e estavel.
    """
    still_added = list(added)
    still_removed = list(removed)
    renamed: list[FundChange] = []

    candidates_added = [f for f in still_added if not f.cnpj]
    candidates_removed = [f for f in still_removed if not f.cnpj]

    for new_fund in candidates_added:
        best: FundOffer | None = None
        best_score = 0.0
        for old_fund in candidates_removed:
            if old_fund not in still_removed:
                continue
            score = _similarity(name_slug(old_fund.name), name_slug(new_fund.name))
            if score > best_score:
                best, best_score = old_fund, score

        if best is not None and best_score >= FUZZY_RENAME_THRESHOLD:
            still_added.remove(new_fund)
            still_removed.remove(best)
            changes = [FieldChange(field="name", old=best.name, new=new_fund.name)]
            changes.extend(_field_changes(best, new_fund))
            renamed.append(
                FundChange(identity=new_fund.identity, name=new_fund.name, changes=changes)
            )

    return still_added, still_removed, renamed


def diff_shelf(
    current: ShelfSnapshot, previous: ShelfSnapshot | None
) -> ShelfDiff:
    """Compara duas fotos da prateleira de uma plataforma."""
    diff = ShelfDiff(
        platform=current.platform,
        prev_date=previous.snapshot_date if previous else None,
        curr_date=current.snapshot_date,
    )

    if current.status == SnapshotStatus.UNAVAILABLE:
        diff.suspect = True
        diff.suspect_reason = current.error or "coleta indisponivel"
        return diff

    if previous is None:
        # Primeira coleta: tudo e "novo", mas isso nao e novidade competitiva.
        diff.added = list(current.funds)
        return diff

    current_index = _index(current.funds)
    previous_index = _index(previous.funds)

    added = [f for identity, f in current_index.items() if identity not in previous_index]
    removed = [f for identity, f in previous_index.items() if identity not in current_index]

    changed: list[FundChange] = []
    for identity, new_fund in current_index.items():
        old_fund = previous_index.get(identity)
        if not old_fund:
            continue
        field_changes = _field_changes(old_fund, new_fund)
        if field_changes:
            changed.append(
                FundChange(identity=identity, name=new_fund.name, changes=field_changes)
            )

    added, removed, renamed = _reconcile_renames(added, removed)
    changed.extend(renamed)

    diff.added = sorted(added, key=lambda f: f.name)
    diff.changed = sorted(changed, key=lambda c: c.name)

    shrink_ratio = (
        len(current.funds) / len(previous.funds) if previous.funds else 1.0
    )
    if current.status == SnapshotStatus.PARTIAL:
        diff.suspect = True
        diff.suspect_reason = current.error or "coleta parcial"
    elif shrink_ratio < SUSPECT_SHRINK_RATIO:
        diff.suspect = True
        diff.suspect_reason = (
            f"coleta atual tem {len(current.funds)} fundos contra "
            f"{len(previous.funds)} da anterior ({shrink_ratio:.0%})"
        )

    # Coleta suspeita: as "saidas" provavelmente sao da coleta, nao da prateleira.
    diff.removed = [] if diff.suspect else sorted(removed, key=lambda f: f.name)
    return diff


def diff_cvm(current: CvmSnapshot, previous: CvmSnapshot | None) -> CvmDiff:
    """Novos registros, mudancas de situacao e cancelamentos na watchlist."""
    diff = CvmDiff(
        prev_date=previous.snapshot_date if previous else None,
        curr_date=current.snapshot_date,
    )

    if current.status == SnapshotStatus.UNAVAILABLE:
        return diff

    if previous is None:
        # Sem baseline, o unico sinal confiavel sao os pre-operacionais abertos.
        diff.new_registrations = sorted(
            [f for f in current.funds if f.is_pre_operational],
            key=lambda f: f.registered_at or "",
            reverse=True,
        )
        return diff

    current_index: dict[str, CvmFund] = {f.cnpj: f for f in current.funds}
    previous_index: dict[str, CvmFund] = {f.cnpj: f for f in previous.funds}

    diff.new_registrations = sorted(
        [f for cnpj, f in current_index.items() if cnpj not in previous_index],
        key=lambda f: f.registered_at or "",
        reverse=True,
    )
    diff.cancelled = sorted(
        [f for cnpj, f in previous_index.items() if cnpj not in current_index],
        key=lambda f: f.name,
    )

    changes: list[CvmSituationChange] = []
    for cnpj, new_fund in current_index.items():
        old_fund = previous_index.get(cnpj)
        if old_fund and old_fund.situation != new_fund.situation:
            changes.append(
                CvmSituationChange(
                    fund=new_fund,
                    old_situation=old_fund.situation,
                    new_situation=new_fund.situation,
                )
            )
    # Lancamentos primeiro: sao o sinal mais acionavel.
    diff.situation_changes = sorted(
        changes, key=lambda c: (not c.is_launch, c.fund.name)
    )
    return diff
