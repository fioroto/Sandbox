from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sprint_metrics.models import (
    AlertSeverity,
    IterationInfo,
    StruggleAlert,
    StruggleReason,
    TaskAnalysis,
    WorkItemSnapshot,
    WorkItemUpdate,
)

# Thresholds
STAGNATION_DAYS = 2  # dias sem redução de horas
TIME_EXCEEDED_FACTOR = 2.0  # fator sobre estimativa original
BOUNCEBACK_THRESHOLD = 2  # transições de volta
REASSIGNMENT_THRESHOLD = 3  # re-atribuições

ACTIVE_STATES = {"Active", "In Progress", "Committed", "Doing"}
DONE_STATES = {"Done", "Closed", "Resolved", "Completed"}
REVIEW_STATES = {"Review", "Code Review", "Testing", "In Review"}


def analyze_task(
    snapshot: WorkItemSnapshot,
    updates: list[WorkItemUpdate],
    iteration: IterationInfo,
) -> TaskAnalysis:
    """Analyze a single work item for struggle indicators."""
    analysis = TaskAnalysis(
        work_item_id=snapshot.id,
        title=snapshot.title,
        work_item_type=snapshot.work_item_type,
        assigned_to=snapshot.assigned_to,
        state=snapshot.state,
        original_estimate=snapshot.original_estimate,
    )

    _check_remaining_work_history(analysis, updates)
    _check_time_in_progress(analysis, updates, snapshot, iteration)
    _check_bouncebacks(analysis, updates)
    _check_reassignments(analysis, updates, snapshot)
    _check_hours_increasing(analysis, updates, snapshot)

    return analysis


def _check_remaining_work_history(
    analysis: TaskAnalysis, updates: list[WorkItemUpdate]
) -> None:
    """Detect stagnant remaining work (not decreasing for N days)."""
    rw_field = "Microsoft.VSTS.Scheduling.RemainingWork"

    # Build daily remaining work timeline
    daily_rw: dict[str, float] = {}
    for update in updates:
        if rw_field in update.field_changes:
            new_val = update.field_changes[rw_field].new_value
            if new_val is not None:
                try:
                    day_key = update.revised_date.strftime("%Y-%m-%d")
                    daily_rw[day_key] = float(new_val)
                    analysis.remaining_work_history.append(
                        (update.revised_date, float(new_val))
                    )
                except (ValueError, TypeError):
                    pass

    if len(daily_rw) < 2:
        return

    # Check for stagnation: N consecutive days without decrease
    sorted_days = sorted(daily_rw.items())
    stagnant_streak = 0
    max_streak = 0

    for i in range(1, len(sorted_days)):
        prev_val = sorted_days[i - 1][1]
        curr_val = sorted_days[i][1]
        if curr_val >= prev_val and prev_val > 0:
            stagnant_streak += 1
            max_streak = max(max_streak, stagnant_streak)
        else:
            stagnant_streak = 0

    if max_streak >= STAGNATION_DAYS:
        severity = AlertSeverity.HIGH if max_streak >= 4 else AlertSeverity.MEDIUM
        analysis.alerts.append(
            StruggleAlert(
                engineer=analysis.assigned_to,
                work_item_id=analysis.work_item_id,
                work_item_title=analysis.title,
                reason=StruggleReason.STAGNANT_HOURS,
                severity=severity,
                details=f"Remaining work não diminuiu por {max_streak} dias consecutivos",
                days_affected=max_streak,
            )
        )


def _check_time_in_progress(
    analysis: TaskAnalysis,
    updates: list[WorkItemUpdate],
    snapshot: WorkItemSnapshot,
    iteration: IterationInfo,
) -> None:
    """Detect tasks in 'Active/In Progress' for too long vs estimate."""
    state_field = "System.State"

    first_active: datetime | None = None
    last_done: datetime | None = None

    for update in updates:
        if state_field not in update.field_changes:
            continue
        new_state = str(update.field_changes[state_field].new_value or "")

        if new_state in ACTIVE_STATES and first_active is None:
            first_active = update.revised_date
        elif new_state in DONE_STATES:
            last_done = update.revised_date

    if first_active is None:
        return

    end_ref = last_done or datetime.now(timezone.utc)
    days_in_progress = (end_ref - first_active).total_seconds() / 86400
    analysis.days_in_progress = round(days_in_progress, 1)

    # Compare with original estimate (convert hours to approximate days: 6h/day)
    if snapshot.original_estimate and snapshot.original_estimate > 0:
        estimated_days = snapshot.original_estimate / 6.0
        if days_in_progress > estimated_days * TIME_EXCEEDED_FACTOR:
            severity = (
                AlertSeverity.CRITICAL
                if days_in_progress > estimated_days * 3
                else AlertSeverity.HIGH
            )
            analysis.alerts.append(
                StruggleAlert(
                    engineer=analysis.assigned_to,
                    work_item_id=analysis.work_item_id,
                    work_item_title=analysis.title,
                    reason=StruggleReason.TIME_EXCEEDED,
                    severity=severity,
                    details=(
                        f"Em progresso há {days_in_progress:.1f} dias "
                        f"(estimativa: {estimated_days:.1f} dias / {snapshot.original_estimate}h)"
                    ),
                    days_affected=int(days_in_progress),
                )
            )


def _check_bouncebacks(
    analysis: TaskAnalysis, updates: list[WorkItemUpdate]
) -> None:
    """Detect tasks bouncing back from Review/Done to Active."""
    state_field = "System.State"
    bouncebacks = 0

    for update in updates:
        if state_field not in update.field_changes:
            continue
        old_state = str(update.field_changes[state_field].old_value or "")
        new_state = str(update.field_changes[state_field].new_value or "")

        went_back = (
            (old_state in REVIEW_STATES or old_state in DONE_STATES)
            and new_state in ACTIVE_STATES
        )
        if went_back:
            bouncebacks += 1

    analysis.bounceback_count = bouncebacks

    if bouncebacks >= BOUNCEBACK_THRESHOLD:
        severity = AlertSeverity.HIGH if bouncebacks >= 3 else AlertSeverity.MEDIUM
        analysis.alerts.append(
            StruggleAlert(
                engineer=analysis.assigned_to,
                work_item_id=analysis.work_item_id,
                work_item_title=analysis.title,
                reason=StruggleReason.BOUNCEBACK,
                severity=severity,
                details=f"Tarefa retornou de Review/Done para Active {bouncebacks} vezes",
                days_affected=bouncebacks,
            )
        )


def _check_reassignments(
    analysis: TaskAnalysis,
    updates: list[WorkItemUpdate],
    snapshot: WorkItemSnapshot,
) -> None:
    """Detect tasks with multiple re-assignments."""
    assigned_field = "System.AssignedTo"
    reassignments = 0

    for update in updates:
        if assigned_field in update.field_changes:
            old = update.field_changes[assigned_field].old_value
            new = update.field_changes[assigned_field].new_value
            if old != new:
                reassignments += 1

    analysis.reassignment_count = reassignments

    if reassignments >= REASSIGNMENT_THRESHOLD:
        severity = AlertSeverity.MEDIUM if reassignments < 5 else AlertSeverity.HIGH
        analysis.alerts.append(
            StruggleAlert(
                engineer=analysis.assigned_to,
                work_item_id=analysis.work_item_id,
                work_item_title=analysis.title,
                reason=StruggleReason.REASSIGNED,
                severity=severity,
                details=f"Tarefa foi re-atribuída {reassignments} vezes",
                days_affected=reassignments,
            )
        )


def _check_hours_increasing(
    analysis: TaskAnalysis,
    updates: list[WorkItemUpdate],
    snapshot: WorkItemSnapshot,
) -> None:
    """Detect cases where remaining work increased."""
    rw_field = "Microsoft.VSTS.Scheduling.RemainingWork"
    increases = []

    for update in updates:
        if rw_field not in update.field_changes:
            continue
        change = update.field_changes[rw_field]
        old_val = change.old_value
        new_val = change.new_value

        if old_val is not None and new_val is not None:
            try:
                old_f = float(old_val)
                new_f = float(new_val)
                if new_f > old_f and old_f > 0:
                    increases.append((update.revised_date, old_f, new_f))
            except (ValueError, TypeError):
                pass

    if increases:
        total_increase = sum(new - old for _, old, new in increases)
        severity = AlertSeverity.HIGH if total_increase > 8 else AlertSeverity.MEDIUM
        analysis.alerts.append(
            StruggleAlert(
                engineer=analysis.assigned_to,
                work_item_id=analysis.work_item_id,
                work_item_title=analysis.title,
                reason=StruggleReason.HOURS_INCREASING,
                severity=severity,
                details=(
                    f"Remaining work aumentou {len(increases)} vez(es), "
                    f"total +{total_increase:.1f}h"
                ),
                days_affected=len(increases),
            )
        )


def analyze_sprint_struggles(
    work_items: list[WorkItemSnapshot],
    updates_map: dict[int, list[WorkItemUpdate]],
    iteration: IterationInfo,
) -> list[TaskAnalysis]:
    """Analyze all work items in a sprint for struggle indicators."""
    analyses = []
    for wi in work_items:
        updates = updates_map.get(wi.id, [])
        analysis = analyze_task(wi, updates, iteration)
        analyses.append(analysis)

    # Sort by number of alerts (most problematic first)
    analyses.sort(key=lambda a: len(a.alerts), reverse=True)
    return analyses
