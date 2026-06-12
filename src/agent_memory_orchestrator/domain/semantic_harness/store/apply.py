from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..commit_update import GraphUpdateDelta
from ..models import HarnessEdge
from .interfaces import EdgeKey
from .interfaces import HarnessGraphStore


@dataclass(slots=True, frozen=True)
class GraphDeltaApplyResult:
    delta_id: str
    status: str
    created_node_ids: tuple[str, ...]
    skipped_node_ids: tuple[str, ...]
    created_edge_keys: tuple[EdgeKey, ...]
    skipped_edge_keys: tuple[EdgeKey, ...]
    missing_node_ids: tuple[str, ...]
    failure_reasons: tuple[str, ...]

    @property
    def applied(self) -> bool:
        return self.status in {"applied", "noop"}

    def as_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "status": self.status,
            "applied": self.applied,
            "created_node_ids": list(self.created_node_ids),
            "skipped_node_ids": list(self.skipped_node_ids),
            "created_edge_keys": [list(key) for key in self.created_edge_keys],
            "skipped_edge_keys": [list(key) for key in self.skipped_edge_keys],
            "missing_node_ids": list(self.missing_node_ids),
            "failure_reasons": list(self.failure_reasons),
        }


def apply_graph_update_delta(
    store: HarnessGraphStore,
    delta: GraphUpdateDelta,
) -> GraphDeltaApplyResult:
    """Apply a graph update delta idempotently against a graph store.

    Endpoint validation happens before any write. This keeps the in-memory
    behavior compatible with a future transactional persistent adapter.
    """

    failure_reasons: list[str] = []
    if store.repo_id != delta.repo_id:
        failure_reasons.append(f"repo_id_mismatch:{store.repo_id}!={delta.repo_id}")
    missing_node_ids = _missing_edge_endpoints(store, delta)
    if missing_node_ids:
        failure_reasons.append("missing_edge_endpoints")
    if failure_reasons:
        return GraphDeltaApplyResult(
            delta_id=delta.delta_id,
            status="failed",
            created_node_ids=(),
            skipped_node_ids=(),
            created_edge_keys=(),
            skipped_edge_keys=(),
            missing_node_ids=missing_node_ids,
            failure_reasons=tuple(failure_reasons),
        )

    created_node_ids: list[str] = []
    skipped_node_ids: list[str] = []
    for node in delta.created_nodes:
        if store.upsert_node(node):
            created_node_ids.append(node.id)
        else:
            skipped_node_ids.append(node.id)

    created_edge_keys: list[EdgeKey] = []
    skipped_edge_keys: list[EdgeKey] = []
    for edge in delta.created_edges:
        key = _edge_key(edge)
        if store.upsert_edge(edge):
            created_edge_keys.append(key)
        else:
            skipped_edge_keys.append(key)
    status = "applied" if created_node_ids or created_edge_keys else "noop"
    return GraphDeltaApplyResult(
        delta_id=delta.delta_id,
        status=status,
        created_node_ids=tuple(created_node_ids),
        skipped_node_ids=tuple(skipped_node_ids),
        created_edge_keys=tuple(created_edge_keys),
        skipped_edge_keys=tuple(skipped_edge_keys),
        missing_node_ids=(),
        failure_reasons=(),
    )


def _missing_edge_endpoints(store: HarnessGraphStore, delta: GraphUpdateDelta) -> tuple[str, ...]:
    available = set(store.node_ids())
    available.update(node.id for node in delta.created_nodes)
    missing: set[str] = set()
    for edge in delta.created_edges:
        if edge.source_id not in available:
            missing.add(edge.source_id)
        if edge.target_id not in available:
            missing.add(edge.target_id)
    return tuple(sorted(missing))


def _edge_key(edge: HarnessEdge) -> EdgeKey:
    return (edge.source_id, edge.target_id, edge.kind)


__all__ = ["GraphDeltaApplyResult", "apply_graph_update_delta"]
