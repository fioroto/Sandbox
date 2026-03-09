from __future__ import annotations

from datetime import datetime, timezone

from azure.devops.v7_0.work_item_tracking.models import Wiql

from sprint_metrics.client import AzureDevOpsClient
from sprint_metrics.models import IterationInfo, WorkItemSnapshot

WORK_ITEM_FIELDS = [
    "System.Id",
    "System.Title",
    "System.WorkItemType",
    "System.State",
    "System.AssignedTo",
    "System.AreaPath",
    "System.IterationPath",
    "System.CreatedDate",
    "System.ChangedDate",
    "System.Tags",
    "Microsoft.VSTS.Common.StateChangeDate",
    "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "Microsoft.VSTS.Scheduling.RemainingWork",
    "Microsoft.VSTS.Scheduling.CompletedWork",
    "Microsoft.VSTS.Scheduling.StoryPoints",
]


def _get_field(fields: dict, name: str, default=None):
    return fields.get(name, default)


def _parse_date(val: object) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _extract_display_name(assigned_to: object) -> str:
    if assigned_to is None:
        return "Unassigned"
    if isinstance(assigned_to, dict):
        return assigned_to.get("displayName", assigned_to.get("uniqueName", "Unknown"))
    if isinstance(assigned_to, str):
        return assigned_to
    return getattr(assigned_to, "display_name", None) or getattr(assigned_to, "unique_name", "Unknown")


def _to_snapshot(work_item) -> WorkItemSnapshot:
    f = work_item.fields
    return WorkItemSnapshot(
        id=work_item.id,
        title=_get_field(f, "System.Title", ""),
        work_item_type=_get_field(f, "System.WorkItemType", ""),
        state=_get_field(f, "System.State", ""),
        assigned_to=_extract_display_name(_get_field(f, "System.AssignedTo")),
        area_path=_get_field(f, "System.AreaPath", ""),
        iteration_path=_get_field(f, "System.IterationPath", ""),
        original_estimate=_get_field(f, "Microsoft.VSTS.Scheduling.OriginalEstimate"),
        remaining_work=_get_field(f, "Microsoft.VSTS.Scheduling.RemainingWork"),
        completed_work=_get_field(f, "Microsoft.VSTS.Scheduling.CompletedWork"),
        story_points=_get_field(f, "Microsoft.VSTS.Scheduling.StoryPoints"),
        created_date=_parse_date(_get_field(f, "System.CreatedDate")),
        changed_date=_parse_date(_get_field(f, "System.ChangedDate")),
        state_change_date=_parse_date(_get_field(f, "Microsoft.VSTS.Common.StateChangeDate")),
        tags=_get_field(f, "System.Tags", ""),
    )


def get_sprint_work_items(
    client: AzureDevOpsClient, iteration: IterationInfo
) -> list[WorkItemSnapshot]:
    """Fetch all work items for a given iteration."""
    wiql_query = Wiql(
        query=(
            "SELECT [System.Id] FROM WorkItems "
            f"WHERE [System.IterationPath] = '{iteration.path}' "
            "AND [System.WorkItemType] IN ('Task', 'Bug', 'User Story', 'Feature') "
            "ORDER BY [System.Id]"
        )
    )
    result = client.wit_client.query_by_wiql(wiql_query, project=client.project)

    if not result.work_items:
        return []

    ids = [wi.id for wi in result.work_items]
    snapshots = []

    # API supports max 200 IDs per batch
    for i in range(0, len(ids), 200):
        batch_ids = ids[i : i + 200]
        items = client.wit_client.get_work_items(
            ids=batch_ids, fields=WORK_ITEM_FIELDS, project=client.project
        )
        snapshots.extend(_to_snapshot(item) for item in items if item)

    return snapshots


def get_current_sprint_work_items(
    client: AzureDevOpsClient,
) -> list[WorkItemSnapshot]:
    """Fetch work items using @CurrentIteration macro."""
    wiql_query = Wiql(
        query=(
            "SELECT [System.Id] FROM WorkItems "
            "WHERE [System.IterationPath] = @CurrentIteration "
            "AND [System.WorkItemType] IN ('Task', 'Bug', 'User Story', 'Feature') "
            "ORDER BY [System.Id]"
        )
    )
    team_context = {"project": client.project, "team": client.team}
    result = client.wit_client.query_by_wiql(
        wiql_query, project=client.project, team_context=team_context
    )

    if not result.work_items:
        return []

    ids = [wi.id for wi in result.work_items]
    snapshots = []

    for i in range(0, len(ids), 200):
        batch_ids = ids[i : i + 200]
        items = client.wit_client.get_work_items(
            ids=batch_ids, fields=WORK_ITEM_FIELDS, project=client.project
        )
        snapshots.extend(_to_snapshot(item) for item in items if item)

    return snapshots
