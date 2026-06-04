from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ...domain.evidence.models import RawEvidenceRef


@runtime_checkable
class EvidenceStorePort(Protocol):
    """Append-only evidence ledger boundary used by ingestion workflows."""

    def append(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        source_app: str,
        event_name: str,
    ) -> RawEvidenceRef:
        """Persist one raw event and return its immutable reference."""


@runtime_checkable
class SessionDrainPort(Protocol):
    """Closed-session draining boundary used by application workflows."""

    def drain(
        self,
        *,
        limit: int = 500,
        session_id: str = "",
        max_windows: int | None = None,
    ) -> dict[str, Any]: ...

    def pending(self, *, session_id: str = "") -> dict[str, Any]: ...


__all__ = ["EvidenceStorePort", "SessionDrainPort"]
