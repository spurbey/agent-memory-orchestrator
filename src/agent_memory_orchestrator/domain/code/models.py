from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class CodeHunk:
    id: str
    session_id: str
    extraction_run_id: str
    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    patch: str
    commit_id: str
    evidence_ids: tuple[str, ...]
    kind: str = "CodeHunk"
    source: str = "deterministic"
    confidence: float = 1.0
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class CodeNode:
    id: str
    session_id: str
    extraction_run_id: str
    file_path: str
    ast_type: str
    line_start: int
    line_end: int
    content: str
    commit_id: str
    evidence_ids: tuple[str, ...]
    prev_content: str = ""
    ast_status: str = "parsed"
    kind: str = "CodeNode"
    source: str = "deterministic"
    confidence: float = 1.0
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


def _asdict(obj: Any) -> dict[str, Any]:
    return {field_name: getattr(obj, field_name) for field_name in obj.__dataclass_fields__}  # type: ignore[attr-defined]
