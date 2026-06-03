"""Workflow boundary for connector event ingestion."""

from __future__ import annotations

from typing import Any, Protocol


class ConnectorEventHandler(Protocol):
    def handle_event_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Capture one connector event envelope."""

    def finalize_session(self, *, session_id: str, reason: str = "idle_timeout", message_count: int = 0) -> dict[str, Any]:
        """Finalize one connector-backed session."""


class ConnectorIngestionWorkflow:
    """Delegate connector ingestion to the concrete connector service."""

    def __init__(self, connector: ConnectorEventHandler) -> None:
        self.connector = connector

    def ingest(self, envelope: dict[str, Any]) -> dict[str, Any]:
        return self.connector.handle_event_envelope(envelope)

    def finalize(self, *, session_id: str, reason: str = "idle_timeout", message_count: int = 0) -> dict[str, Any]:
        return self.connector.finalize_session(
            session_id=session_id,
            reason=reason,
            message_count=message_count,
        )


__all__ = ["ConnectorEventHandler", "ConnectorIngestionWorkflow"]
