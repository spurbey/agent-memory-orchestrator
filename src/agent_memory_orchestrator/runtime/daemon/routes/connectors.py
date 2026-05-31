"""Daemon connector routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....integrations.connectors.slack import SlackConnectorService

CONNECTOR_ROUTES = ("/api/connectors/slack/status",)

JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_connectors_get(*, path: str, settings: Settings, write_json: JsonWriter) -> bool:
    """Handle connector status routes."""
    if path != "/api/connectors/slack/status":
        return False
    try:
        svc = SlackConnectorService(settings)
        write_json(
            200,
            {
                "ok": True,
                "slack": svc.status(),
                "run_command": "amo-cli slack run --reply-mode answer",
                "behavior": "Answers only when the AMO bot is tagged in a channel or thread.",
            },
        )
    except Exception as exc:
        write_json(500, {"ok": False, "error": str(exc)})
    return True


__all__ = ["CONNECTOR_ROUTES", "handle_connectors_get"]
