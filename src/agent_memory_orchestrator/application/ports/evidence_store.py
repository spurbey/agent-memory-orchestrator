from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class EvidenceStorePort(Protocol):
    """Append-only evidence ledger boundary used by ingestion workflows."""

    def append(self, record: Mapping[str, Any]) -> str:
        """Persist one evidence record and return its stable id."""

    def read_session(self, session_id: str, *, limit: int = 1000) -> Iterable[Mapping[str, Any]]:
        """Read evidence records for one session without mutating the ledger."""
