from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

from ...domain.connectors.responses import ConnectorResponse


@dataclass(slots=True, frozen=True)
class ConnectorEvent:
    source: str
    event_type: str
    text: str = ""
    thread_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Connector(Protocol):
    name: str
    version: str

    def ingest(self, event: ConnectorEvent) -> ConnectorResponse:
        """Normalize external content into AMO evidence-ready events."""

    def respond(self, event: ConnectorEvent, answer: str) -> ConnectorResponse:
        """Send a connector response for explicit bot-addressed interactions."""
