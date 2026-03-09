from __future__ import annotations

from collections import defaultdict
from statistics import median

from sprint_metrics.models import (
    IterationInfo,
    SprintPhase,
    VelocityAggregation,
    VelocityRecord,
    WorkItemSnapshot,
    WorkItemUpdate,
)

DONE_STATES = {"Done", "Closed", "Resolved", "Completed"}


def _determine_sprint_phase(
    completed_date, iteration: IterationInfo
) -> SprintPhase | None:
    if not completed_date or not iteration.start_date or not iteration.end_date:
        return None

    total_duration = (iteration.end_date - iteration.start_date).total_seconds()
    if total_duration <= 0:
        return None

    elapsed = (completed_date - iteration.start_date).total_seconds()
    ratio = elapsed / total_duration

    if ratio <= 0.33:
        return SprintPhase.FIRST_THIRD
    elif ratio <= 0.66:
        return SprintPhase.SECOND_THIRD
    return SprintPhase.LAST_THIRD


def _find_completion_date(updates: list[WorkItemUpdate]):
    """Find the date when the work item was moved to Done/Closed."""
    for update in reversed(updates):
        state_change = update.field_changes.get("System.State")
        if state_change and str(state_change.new_value or "") in DONE_STATES:
            return update.revised_date
    return None


def build_velocity_records(
    work_items: list[WorkItemSnapshot],
    updates_map: dict[int, list[WorkItemUpdate]],
    iteration: IterationInfo,
) -> list[VelocityRecord]:
    """Build velocity records from completed work items in a sprint."""
    records = []

    for wi in work_items:
        if wi.state not in DONE_STATES:
            continue

        updates = updates_map.get(wi.id, [])
        completed_date = _find_completion_date(updates)

        # Calculate hours spent
        hours_spent = 0.0
        if wi.completed_work and wi.completed_work > 0:
            hours_spent = wi.completed_work
        elif wi.original_estimate is not None and wi.remaining_work is not None:
            hours_spent = max(0, wi.original_estimate - wi.remaining_work)
        elif wi.original_estimate:
            hours_spent = wi.original_estimate

        if hours_spent <= 0:
            continue

        sprint_phase = _determine_sprint_phase(completed_date, iteration)
        day_of_week = completed_date.weekday() if completed_date else 0

        records.append(
            VelocityRecord(
                engineer=wi.assigned_to,
                project=wi.area_path.split("\\")[0] if wi.area_path else "",
                area_path=wi.area_path,
                work_item_type=wi.work_item_type,
                story_points=wi.story_points,
                hours_spent=hours_spent,
                sprint_name=iteration.name,
                sprint_phase=sprint_phase,
                day_of_week=day_of_week,
                completed_date=completed_date,
            )
        )

    return records


def _aggregate(
    records: list[VelocityRecord], key_fn, dimension_name: str
) -> list[VelocityAggregation]:
    """Group records by a key function and compute aggregated metrics."""
    groups: dict[str, list[VelocityRecord]] = defaultdict(list)
    for r in records:
        key = str(key_fn(r))
        groups[key].append(r)

    aggregations = []
    for dim_value, group in sorted(groups.items()):
        total_hours = sum(r.hours_spent for r in group)
        total_points = sum(r.story_points or 0 for r in group)

        hours_per_point = None
        median_hpp = None
        if total_points > 0:
            hours_per_point = total_hours / total_points
            per_item_hpp = [
                r.hours_spent / r.story_points
                for r in group
                if r.story_points and r.story_points > 0
            ]
            if per_item_hpp:
                median_hpp = median(per_item_hpp)

        aggregations.append(
            VelocityAggregation(
                dimension_name=dimension_name,
                dimension_value=dim_value,
                avg_hours_per_point=round(hours_per_point, 2) if hours_per_point else None,
                median_hours_per_point=round(median_hpp, 2) if median_hpp else None,
                total_hours=round(total_hours, 1),
                total_points=round(total_points, 1),
                sample_count=len(group),
            )
        )

    return aggregations


def _complexity_bucket(sp: float | None) -> str:
    if sp is None:
        return "N/A"
    if sp <= 3:
        return "Small (1-3)"
    if sp <= 8:
        return "Medium (5-8)"
    return "Large (13+)"


DAY_NAMES = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


def compute_velocity_by_dimensions(
    records: list[VelocityRecord],
) -> dict[str, list[VelocityAggregation]]:
    """Compute velocity aggregations across all dimensions."""
    if not records:
        return {}

    return {
        "Por Engenheiro": _aggregate(records, lambda r: r.engineer, "Engenheiro"),
        "Por Tipo de Tarefa": _aggregate(
            records, lambda r: r.work_item_type, "Tipo"
        ),
        "Por Projeto": _aggregate(records, lambda r: r.project, "Projeto"),
        "Por Dia da Semana": _aggregate(
            records, lambda r: DAY_NAMES[r.day_of_week], "Dia"
        ),
        "Por Fase da Sprint": _aggregate(
            records,
            lambda r: r.sprint_phase.value if r.sprint_phase else "N/A",
            "Fase",
        ),
        "Por Complexidade": _aggregate(
            records, lambda r: _complexity_bucket(r.story_points), "Complexidade"
        ),
    }
