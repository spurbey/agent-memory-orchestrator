"""Application boundary for append-only evidence ingestion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...domain.evidence import RawEvidenceRef
from ...domain.evidence import RawEvidenceStore


class EvidenceIngestService:
    """Thin service wrapper around the raw evidence ledger."""

    def __init__(self, evidence_dir: Path, *, store: RawEvidenceStore | None = None) -> None:
        self.store = store or RawEvidenceStore(evidence_dir)

    def append(
        self,
        payload: dict[str, Any],
        *,
        session_id: str,
        source_app: str,
        event_name: str,
    ) -> RawEvidenceRef:
        return self.store.append(
            payload,
            session_id=session_id,
            source_app=source_app,
            event_name=event_name,
        )


__all__ = ["EvidenceIngestService"]
