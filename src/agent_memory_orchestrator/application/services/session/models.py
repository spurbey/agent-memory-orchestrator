from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .constants import DEFAULT_CODE_EMBEDDING_MODEL


@dataclass(frozen=True)
class SessionGraphBuildOptions:
    session_id: str
    graph_path: Path
    repo_root: Path
    commit: str
    evidence_paths: tuple[Path, ...] = ()
    transcript_paths: tuple[Path, ...] = ()
    file_paths: tuple[str, ...] = ()
    text_embedding_model: str = "BAAI/bge-m3"
    code_embedding_model: str = DEFAULT_CODE_EMBEDDING_MODEL
    force: bool = False
    limit_events: int | None = None


@dataclass(frozen=True)
class SessionGraphQueryOptions:
    graph_path: Path
    query: str | None = None
    code_query: str | None = None
    text_embedding_model: str = "BAAI/bge-m3"
    code_embedding_model: str = DEFAULT_CODE_EMBEDDING_MODEL
    limit: int = 8


@dataclass
class SessionGraphBuildResult:
    ok: bool
    graph_path: str
    session_id: str
    extraction_run_id: str
    counts: dict[str, int] = field(default_factory=dict)
    ast_status_counts: dict[str, int] = field(default_factory=dict)
    edge_kinds: dict[str, int] = field(default_factory=dict)
    models: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class SessionGraphSearchHit:
    node_id: str
    kind: str
    label: str
    summary: str
    score: float
    evidence_id: str = ""
    commit_id: str = ""
    neighbors: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionGraphQueryResult:
    ok: bool
    graph_path: str
    text_hits: list[SessionGraphSearchHit] = field(default_factory=list)
    code_hits: list[SessionGraphSearchHit] = field(default_factory=list)
    models: dict[str, Any] = field(default_factory=dict)
    diagnostics: list[str] = field(default_factory=list)


__all__ = [
    "SessionGraphBuildOptions",
    "SessionGraphBuildResult",
    "SessionGraphQueryOptions",
    "SessionGraphQueryResult",
    "SessionGraphSearchHit",
]
