"""Application boundary for external connector runtimes."""

from __future__ import annotations

from typing import Any

from ....core.config import Settings
from ....integrations.connectors.slack import SlackConnectorService


class ConnectorRuntimeService:
    """Factory boundary for connector runtime services."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def slack(self, **kwargs: Any) -> SlackConnectorService:
        return SlackConnectorService(self.settings, **kwargs)


__all__ = ["ConnectorRuntimeService", "SlackConnectorService"]
