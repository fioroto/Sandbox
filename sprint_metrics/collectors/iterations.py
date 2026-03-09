from __future__ import annotations

from datetime import datetime, timezone

from azure.devops.v7_0.work.models import TeamContext

from sprint_metrics.client import AzureDevOpsClient
from sprint_metrics.models import IterationInfo


def _parse_date(date_val: object) -> datetime | None:
    if date_val is None:
        return None
    if isinstance(date_val, datetime):
        return date_val
    if isinstance(date_val, str):
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(date_val, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def get_team_iterations(client: AzureDevOpsClient) -> list[IterationInfo]:
    """Fetch all iterations for the configured team."""
    team_context = TeamContext(project=client.project, team=client.team)
    iterations = client.work_client.get_team_iterations(team_context)

    now = datetime.now(timezone.utc)
    result = []

    for it in iterations:
        start = _parse_date(getattr(it.attributes, "start_date", None))
        end = _parse_date(getattr(it.attributes, "finish_date", None))

        is_current = False
        if start and end:
            is_current = start <= now <= end

        result.append(
            IterationInfo(
                id=it.id,
                name=it.name,
                path=it.path,
                start_date=start,
                end_date=end,
                is_current=is_current,
            )
        )

    return result


def get_current_iteration(client: AzureDevOpsClient) -> IterationInfo | None:
    """Return the current sprint iteration."""
    iterations = get_team_iterations(client)
    for it in iterations:
        if it.is_current:
            return it
    return None


def get_recent_iterations(
    client: AzureDevOpsClient, count: int = 6
) -> list[IterationInfo]:
    """Return the N most recent completed iterations (excluding current)."""
    iterations = get_team_iterations(client)
    now = datetime.now(timezone.utc)

    completed = [
        it for it in iterations if it.end_date and it.end_date < now and not it.is_current
    ]
    completed.sort(key=lambda x: x.end_date or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return completed[:count]
