"""Source-neutral connector event models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class ConnectorEvent:
    """Normalized external event ready for append-only evidence capture."""

    connector: str
    source_app: str
    external_id: str
    session_id: str
    event_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_evidence_payload(self) -> dict[str, Any]:
        return {
            "connector": self.connector,
            "source_app": self.source_app,
            "external_id": self.external_id,
            "session_id": self.session_id,
            "event_type": self.event_type,
            "message": self.content,
            "content": self.content,
            "metadata": self.metadata,
        }


__all__ = ["ConnectorEvent"]
