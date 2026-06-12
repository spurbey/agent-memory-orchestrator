from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness import GraphDeltaApplyResult
from agent_memory_orchestrator.domain.semantic_harness import HarnessProjectionSet
from agent_memory_orchestrator.domain.semantic_harness import GraphSnapshotIdentity


@dataclass(slots=True, frozen=True)
class HarnessRuntimeBootstrapResult:
    repo_root: Path
    repo_id: str
    file_count: int
    skipped_count: int
    skipped: tuple[dict[str, str], ...]
    graph_snapshot: GraphSnapshotIdentity
    projection_id: str
    projection_document_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": str(self.repo_root),
            "repo_id": self.repo_id,
            "file_count": self.file_count,
            "skipped_count": self.skipped_count,
            "skipped": list(self.skipped),
            "graph_snapshot": self.graph_snapshot.as_dict(),
            "projection_id": self.projection_id,
            "projection_document_count": self.projection_document_count,
        }


@dataclass(slots=True, frozen=True)
class HarnessRuntimeDeltaApplyResult:
    repo_id: str
    graph_snapshot: GraphSnapshotIdentity
    projection: HarnessProjectionSet
    apply_result: GraphDeltaApplyResult

    @property
    def applied(self) -> bool:
        return self.apply_result.applied

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "graph_snapshot": self.graph_snapshot.as_dict(),
            "projection": self.projection.as_dict(),
            "apply_result": self.apply_result.as_dict(),
            "applied": self.applied,
        }


__all__ = ["HarnessRuntimeBootstrapResult", "HarnessRuntimeDeltaApplyResult"]
