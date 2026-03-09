from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AlertSeverity(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class StruggleReason(Enum):
    STAGNANT_HOURS = "Horas restantes estagnadas"
    TIME_EXCEEDED = "Tempo em progresso acima do esperado"
    BOUNCEBACK = "Retrabalho (retornou de Review/Done)"
    REASSIGNED = "Re-atribuições múltiplas"
    HOURS_INCREASING = "Horas restantes aumentando"


class SprintPhase(Enum):
    FIRST_THIRD = "1o terço"
    SECOND_THIRD = "2o terço"
    LAST_THIRD = "3o terço"


@dataclass
class IterationInfo:
    id: str
    name: str
    path: str
    start_date: datetime | None
    end_date: datetime | None
    is_current: bool = False


@dataclass
class WorkItemSnapshot:
    id: int
    title: str
    work_item_type: str
    state: str
    assigned_to: str
    area_path: str
    iteration_path: str
    original_estimate: float | None
    remaining_work: float | None
    completed_work: float | None
    story_points: float | None
    created_date: datetime | None
    changed_date: datetime | None
    state_change_date: datetime | None
    tags: str = ""


@dataclass
class FieldChange:
    field_name: str
    old_value: object
    new_value: object


@dataclass
class WorkItemUpdate:
    revision: int
    revised_date: datetime
    revised_by: str
    field_changes: dict[str, FieldChange] = field(default_factory=dict)


@dataclass
class StruggleAlert:
    engineer: str
    work_item_id: int
    work_item_title: str
    reason: StruggleReason
    severity: AlertSeverity
    details: str
    days_affected: int = 0


@dataclass
class TaskAnalysis:
    work_item_id: int
    title: str
    work_item_type: str
    assigned_to: str
    state: str
    days_in_progress: float = 0.0
    bounceback_count: int = 0
    reassignment_count: int = 0
    remaining_work_history: list[tuple[datetime, float]] = field(default_factory=list)
    original_estimate: float | None = None
    alerts: list[StruggleAlert] = field(default_factory=list)


@dataclass
class VelocityRecord:
    engineer: str
    project: str
    area_path: str
    work_item_type: str
    story_points: float | None
    hours_spent: float
    sprint_name: str
    sprint_phase: SprintPhase | None
    day_of_week: int  # 0=Monday, 6=Sunday
    completed_date: datetime | None


@dataclass
class VelocityAggregation:
    dimension_name: str
    dimension_value: str
    avg_hours_per_point: float | None
    median_hours_per_point: float | None
    total_hours: float
    total_points: float
    sample_count: int


@dataclass
class HealthMetrics:
    wip_per_engineer: dict[str, int] = field(default_factory=dict)
    blocked_items: list[dict] = field(default_factory=list)
    carry_over_rate: float = 0.0
    carry_over_items: list[dict] = field(default_factory=list)
    burndown_deviation: float = 0.0
    ideal_remaining: float = 0.0
    actual_remaining: float = 0.0
    task_switching: dict[str, int] = field(default_factory=dict)


@dataclass
class SprintReport:
    sprint: IterationInfo
    struggle_alerts: list[StruggleAlert] = field(default_factory=list)
    task_analyses: list[TaskAnalysis] = field(default_factory=list)
    velocity_by_dimension: dict[str, list[VelocityAggregation]] = field(
        default_factory=dict
    )
    health: HealthMetrics = field(default_factory=HealthMetrics)
