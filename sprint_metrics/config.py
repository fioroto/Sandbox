import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    def __init__(
        self,
        org_url: str | None = None,
        pat: str | None = None,
        project: str | None = None,
        team: str | None = None,
        history_sprints: int = 6,
    ):
        self.org_url = org_url or os.getenv("AZURE_DEVOPS_ORG_URL", "")
        self.pat = pat or os.getenv("AZURE_DEVOPS_PAT", "")
        self.project = project or os.getenv("AZURE_DEVOPS_PROJECT", "")
        self.team = team or os.getenv("AZURE_DEVOPS_TEAM", "")
        self.history_sprints = history_sprints

    def validate(self) -> list[str]:
        errors = []
        if not self.org_url:
            errors.append("AZURE_DEVOPS_ORG_URL is required")
        if not self.pat:
            errors.append("AZURE_DEVOPS_PAT is required")
        if not self.project:
            errors.append("AZURE_DEVOPS_PROJECT is required")
        if not self.team:
            errors.append("AZURE_DEVOPS_TEAM is required")
        return errors
