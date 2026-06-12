from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .models import StructuralHarnessGraph

GRAPH_SCHEMA_VERSION = "semantic_harness_graph_v1"


@dataclass(slots=True, frozen=True)
class GraphSnapshotIdentity:
    repo_id: str
    graph_schema_version: str
    graph_snapshot_id: str
    node_count: int
    edge_count: int

    def as_dict(self) -> dict[str, str | int]:
        return {
            "repo_id": self.repo_id,
            "graph_schema_version": self.graph_schema_version,
            "graph_snapshot_id": self.graph_snapshot_id,
            "node_count": self.node_count,
            "edge_count": self.edge_count,
        }


def graph_snapshot_identity(
    graph: StructuralHarnessGraph,
    *,
    graph_schema_version: str = GRAPH_SCHEMA_VERSION,
) -> GraphSnapshotIdentity:
    """Return structural graph identity for caches, projections, and eval replay.

    The snapshot intentionally hashes node IDs and edge keys only. Mutable
    operational metadata, summaries, weights, and counters must not invalidate
    structural snapshot identity.
    """

    return GraphSnapshotIdentity(
        repo_id=graph.repo_id,
        graph_schema_version=graph_schema_version,
        graph_snapshot_id=graph_snapshot_id(graph, graph_schema_version=graph_schema_version),
        node_count=len(graph.nodes),
        edge_count=len(graph.edges),
    )


def graph_snapshot_id(
    graph: StructuralHarnessGraph,
    *,
    graph_schema_version: str = GRAPH_SCHEMA_VERSION,
) -> str:
    node_ids = sorted(node.id for node in graph.nodes)
    edge_keys = sorted(_edge_key(edge.source_id, edge.kind, edge.target_id) for edge in graph.edges)
    stable = "\n".join((graph_schema_version, graph.repo_id, "nodes", *node_ids, "edges", *edge_keys))
    return f"gsnap:{_safe_repo(graph.repo_id)}:{_short_hash(stable, size=24)}"


def _edge_key(source_id: str, kind: str, target_id: str) -> str:
    return "|".join((source_id, kind, target_id))


def _safe_repo(repo_id: str) -> str:
    return str(repo_id or "").strip() or "repo:unknown"


def _short_hash(value: str, *, size: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


__all__ = ["GRAPH_SCHEMA_VERSION", "GraphSnapshotIdentity", "graph_snapshot_id", "graph_snapshot_identity"]
