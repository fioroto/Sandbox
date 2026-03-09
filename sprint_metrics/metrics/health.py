from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from sprint_metrics.models import (
    HealthMetrics,
    IterationInfo,
    WorkItemSnapshot,
    WorkItemUpdate,
)

ACTIVE_STATES = {"Active", "In Progress", "Committed", "Doing"}
DONE_STATES = {"Done", "Closed", "Resolved", "Completed"}
BLOCKED_TAGS = {"blocked", "impediment", "bloqueado"}
BLOCKED_STATES = {"Blocked", "On Hold"}


def compute_wip_per_engineer(
    work_items: list[WorkItemSnapshot],
) -> dict[str, int]:
    """Count work items in active states per engineer."""
    wip: dict[str, int] = defaultdict(int)
    for wi in work_items:
        if wi.state in ACTIVE_STATES:
            wip[wi.assigned_to] += 1
    return dict(wip)


def compute_blocked_items(
    work_items: list[WorkItemSnapshot],
) -> list[dict]:
    """Find blocked items and calculate aging (days blocked)."""
    now = datetime.now(timezone.utc)
    blocked = []

    for wi in work_items:
        is_blocked = wi.state in BLOCKED_STATES
        if not is_blocked and wi.tags:
            item_tags = {t.strip().lower() for t in wi.tags.split(";")}
            is_blocked = bool(item_tags & BLOCKED_TAGS)

        if is_blocked:
            days_blocked = 0
            if wi.state_change_date:
                days_blocked = (now - wi.state_change_date).days

            blocked.append(
                {
                    "id": wi.id,
                    "title": wi.title,
                    "assigned_to": wi.assigned_to,
                    "days_blocked": days_blocked,
                    "state": wi.state,
                }
            )

    blocked.sort(key=lambda x: x["days_blocked"], reverse=True)
    return blocked


def compute_carry_over(
    current_items: list[WorkItemSnapshot],
    previous_items: list[WorkItemSnapshot],
) -> tuple[float, list[dict]]:
    """Calculate carry-over rate: items from previous sprint that are incomplete."""
    if not previous_items:
        return 0.0, []

    prev_incomplete_ids = {
        wi.id for wi in previous_items if wi.state not in DONE_STATES
    }
    current_ids = {wi.id for wi in current_items}

    carried_over = prev_incomplete_ids & current_ids
    carry_over_details = []

    for wi in current_items:
        if wi.id in carried_over:
            carry_over_details.append(
                {
                    "id": wi.id,
                    "title": wi.title,
                    "assigned_to": wi.assigned_to,
                    "state": wi.state,
                    "remaining_work": wi.remaining_work,
                }
            )

    rate = len(carried_over) / len(previous_items) if previous_items else 0.0
    return round(rate * 100, 1), carry_over_details


def compute_burndown_deviation(
    work_items: list[WorkItemSnapshot],
    iteration: IterationInfo,
) -> tuple[float, float, float]:
    """
    Calculate burndown deviation from ideal line.

    Returns: (deviation_percentage, ideal_remaining, actual_remaining)
    """
    if not iteration.start_date or not iteration.end_date:
        return 0.0, 0.0, 0.0

    now = datetime.now(timezone.utc)
    total_duration = (iteration.end_date - iteration.start_date).total_seconds()
    elapsed = (min(now, iteration.end_date) - iteration.start_date).total_seconds()

    if total_duration <= 0:
        return 0.0, 0.0, 0.0

    progress_ratio = max(0, min(1, elapsed / total_duration))

    total_estimate = sum(
        wi.original_estimate or 0 for wi in work_items
    )
    actual_remaining = sum(
        wi.remaining_work or 0 for wi in work_items if wi.state not in DONE_STATES
    )
    ideal_remaining = total_estimate * (1 - progress_ratio)

    if total_estimate <= 0:
        return 0.0, 0.0, actual_remaining

    deviation = ((actual_remaining - ideal_remaining) / total_estimate) * 100
    return round(deviation, 1), round(ideal_remaining, 1), round(actual_remaining, 1)


def compute_task_switching(
    updates_map: dict[int, list[WorkItemUpdate]],
    work_items: list[WorkItemSnapshot],
) -> dict[str, int]:
    """
    Count task switching per engineer: how many different tasks
    an engineer touched on the same day.
    """
    wi_map = {wi.id: wi for wi in work_items}

    # engineer -> date -> set of work item ids
    engineer_daily: dict[str, dict[str, set[int]]] = defaultdict(
        lambda: defaultdict(set)
    )

    for wid, updates in updates_map.items():
        wi = wi_map.get(wid)
        if not wi:
            continue
        for update in updates:
            day = update.revised_date.strftime("%Y-%m-%d")
            engineer_daily[update.revised_by][day].add(wid)

    # Count days where engineer touched more than 1 task
    switching: dict[str, int] = {}
    for engineer, daily in engineer_daily.items():
        multi_task_days = sum(1 for tasks in daily.values() if len(tasks) > 1)
        if multi_task_days > 0:
            switching[engineer] = multi_task_days

    return switching


def compute_health_metrics(
    current_items: list[WorkItemSnapshot],
    previous_items: list[WorkItemSnapshot],
    updates_map: dict[int, list[WorkItemUpdate]],
    iteration: IterationInfo,
) -> HealthMetrics:
    """Compute all health metrics for the sprint."""
    wip = compute_wip_per_engineer(current_items)
    blocked = compute_blocked_items(current_items)
    carry_rate, carry_items = compute_carry_over(current_items, previous_items)
    deviation, ideal, actual = compute_burndown_deviation(current_items, iteration)
    switching = compute_task_switching(updates_map, current_items)

    return HealthMetrics(
        wip_per_engineer=wip,
        blocked_items=blocked,
        carry_over_rate=carry_rate,
        carry_over_items=carry_items,
        burndown_deviation=deviation,
        ideal_remaining=ideal,
        actual_remaining=actual,
        task_switching=switching,
    )
