from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Protocol


class GraphBackendUnavailable(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    summary: str = ""
    status: str = "draft"
    scope: str = "session"
    session_id: str = ""
    project_id: str = "default"
    source_app: str = "unknown"
    evidence_id: str = ""
    commit_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "summary": self.summary,
            "status": self.status,
            "scope": self.scope,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "source_app": self.source_app,
            "evidence_id": self.evidence_id,
            "commit_id": self.commit_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    kind: str
    weight: float = 1.0
    confidence: float = 0.8
    evidence_id: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "weight": self.weight,
            "confidence": self.confidence,
            "evidence_id": self.evidence_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class GraphStore(Protocol):
    def init_schema(self) -> None:
        """Create graph schema if missing."""

    def upsert_node(self, node: GraphNode) -> None:
        """Create or update one graph node."""

    def upsert_edge(self, edge: GraphEdge) -> None:
        """Create or replace one graph edge."""

    def search_nodes(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        """Search graph nodes by text."""

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        """List graph nodes with simple filters."""

    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return adjacent nodes."""

    def list_edges(
        self,
        *,
        limit: int = 100,
        session_id: str = "",
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List graph edges with simple filters."""

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        """Return merge/graph status."""

    def set_node_status(self, node_id: str, status: str) -> bool:
        """Update a node status."""

    def close(self) -> None:
        """Close backend resources."""
