"""Application boundary for closed-session detection and enqueueing."""

from __future__ import annotations

from typing import Any

from ...domain.evidence import EvidenceDrain


class SessionBoundaryService:
    """Wrap daemon-side evidence drain behavior behind an application service."""

    def __init__(self, drain: EvidenceDrain) -> None:
        self.drain = drain

    def drain_closed_sessions(
        self,
        *,
        limit: int = 500,
        session_id: str = "",
        max_windows: int | None = None,
    ) -> dict[str, Any]:
        return self.drain.drain(limit=limit, session_id=session_id, max_windows=max_windows)

    def pending(self, *, session_id: str = "") -> dict[str, Any]:
        return self.drain.pending(session_id=session_id)


__all__ = ["SessionBoundaryService"]
