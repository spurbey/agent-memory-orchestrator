from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class TriggerDecision:
    should_process: bool
    trigger_type: str
    reason: str
    is_write: bool = False
    is_test: bool = False
    is_git: bool = False
    is_commit: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "should_process": self.should_process,
            "trigger_type": self.trigger_type,
            "reason": self.reason,
            "is_write": self.is_write,
            "is_test": self.is_test,
            "is_git": self.is_git,
            "is_commit": self.is_commit,
        }


@dataclass(slots=True, frozen=True)
class RawEvidenceRef:
    id: str
    hash: str
    path: str
    offset: int
    session_id: str
    source_app: str
    event_name: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "hash": self.hash,
            "path": self.path,
            "offset": self.offset,
            "session_id": self.session_id,
            "source_app": self.source_app,
            "event_name": self.event_name,
            "created_at": self.created_at,
        }


@dataclass(slots=True)
class DrainSessionState:
    pending_count: int = 0
    first_event_id: str = ""
    latest_event_id: str = ""
    source_app: str = ""
    repo_path: str = ""
    evidence_days: set[str] = field(default_factory=set)
    enqueued_windows: int = 0
    pending_records: list[dict[str, Any]] = field(default_factory=list)

__all__ = ["DrainSessionState", "RawEvidenceRef", "TriggerDecision"]
