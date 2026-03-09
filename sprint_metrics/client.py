from azure.devops.connection import Connection
from msrest.authentication import BasicAuthentication

from sprint_metrics.config import Config


class AzureDevOpsClient:
    def __init__(self, config: Config):
        self._config = config
        credentials = BasicAuthentication("", config.pat)
        self._connection = Connection(base_url=config.org_url, creds=credentials)

    @property
    def wit_client(self):
        return self._connection.clients.get_work_item_tracking_client()

    @property
    def work_client(self):
        return self._connection.clients.get_work_client()

    @property
    def project(self) -> str:
        return self._config.project

    @property
    def team(self) -> str:
        return self._config.team
