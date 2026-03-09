from __future__ import annotations

from datetime import datetime, timezone

from sprint_metrics.client import AzureDevOpsClient
from sprint_metrics.models import FieldChange, WorkItemUpdate

TRACKED_FIELDS = {
    "System.State",
    "System.AssignedTo",
    "System.Reason",
    "Microsoft.VSTS.Scheduling.RemainingWork",
    "Microsoft.VSTS.Scheduling.CompletedWork",
    "Microsoft.VSTS.Scheduling.OriginalEstimate",
    "Microsoft.VSTS.Common.StateChangeDate",
}


def _parse_date(val: object) -> datetime:
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(val, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return datetime.now(timezone.utc)


def _extract_display_name(val: object) -> str:
    if val is None:
        return "Unassigned"
    if isinstance(val, dict):
        return val.get("displayName", val.get("uniqueName", "Unknown"))
    if isinstance(val, str):
        return val
    return getattr(val, "display_name", None) or getattr(val, "unique_name", "Unknown")


def get_work_item_updates(
    client: AzureDevOpsClient, work_item_id: int
) -> list[WorkItemUpdate]:
    """Fetch all updates for a work item, extracting tracked field changes."""
    raw_updates = client.wit_client.get_updates(work_item_id, project=client.project)
    updates = []

    for raw in raw_updates:
        if not raw.fields:
            continue

        field_changes = {}
        for field_name, change in raw.fields.items():
            if field_name not in TRACKED_FIELDS:
                continue

            old_val = getattr(change, "old_value", None) if change else None
            new_val = getattr(change, "new_value", None) if change else None

            # Handle dict-style access as fallback
            if old_val is None and isinstance(change, dict):
                old_val = change.get("oldValue")
                new_val = change.get("newValue")

            # Normalize AssignedTo to display name
            if field_name == "System.AssignedTo":
                old_val = _extract_display_name(old_val)
                new_val = _extract_display_name(new_val)

            field_changes[field_name] = FieldChange(
                field_name=field_name,
                old_value=old_val,
                new_value=new_val,
            )

        if field_changes:
            revised_date = _parse_date(
                getattr(raw, "revised_date", None)
                or getattr(raw, "revisedDate", None)
            )
            revised_by = _extract_display_name(
                getattr(raw, "revised_by", None)
                or getattr(raw, "revisedBy", None)
            )

            updates.append(
                WorkItemUpdate(
                    revision=getattr(raw, "rev", 0) or getattr(raw, "id", 0),
                    revised_date=revised_date,
                    revised_by=revised_by,
                    field_changes=field_changes,
                )
            )

    return updates


def get_batch_updates(
    client: AzureDevOpsClient, work_item_ids: list[int]
) -> dict[int, list[WorkItemUpdate]]:
    """Fetch updates for multiple work items."""
    return {
        wid: get_work_item_updates(client, wid)
        for wid in work_item_ids
    }
