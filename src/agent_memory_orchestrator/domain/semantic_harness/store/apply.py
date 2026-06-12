from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..commit_update import GraphUpdateDelta
from ..models import HarnessEdge
from ..models import HarnessNode
from .interfaces import EdgeKey
from .interfaces import HarnessGraphStore


@dataclass(slots=True, frozen=True)
class GraphDeltaApplyResult:
    delta_id: str
    status: str
    created_node_ids: tuple[str, ...]
    updated_node_ids: tuple[str, ...]
    skipped_node_ids: tuple[str, ...]
    created_edge_keys: tuple[EdgeKey, ...]
    updated_edge_keys: tuple[EdgeKey, ...]
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
            "updated_node_ids": list(self.updated_node_ids),
            "skipped_node_ids": list(self.skipped_node_ids),
            "created_edge_keys": [list(key) for key in self.created_edge_keys],
            "updated_edge_keys": [list(key) for key in self.updated_edge_keys],
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
            updated_node_ids=(),
            skipped_node_ids=(),
            created_edge_keys=(),
            updated_edge_keys=(),
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

    updated_node_ids = _update_changed_entity_stats(store, delta)

    created_edge_keys: list[EdgeKey] = []
    updated_edge_keys: list[EdgeKey] = []
    skipped_edge_keys: list[EdgeKey] = []
    for edge in delta.created_edges:
        key = _edge_key(edge)
        edge_result = _apply_edge(store, edge)
        if edge_result == "created":
            created_edge_keys.append(key)
        elif edge_result == "updated":
            updated_edge_keys.append(key)
        else:
            skipped_edge_keys.append(key)
    status = "applied" if created_node_ids or updated_node_ids or created_edge_keys or updated_edge_keys else "noop"
    return GraphDeltaApplyResult(
        delta_id=delta.delta_id,
        status=status,
        created_node_ids=tuple(created_node_ids),
        updated_node_ids=updated_node_ids,
        skipped_node_ids=tuple(skipped_node_ids),
        created_edge_keys=tuple(created_edge_keys),
        updated_edge_keys=tuple(updated_edge_keys),
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


def _apply_edge(store: HarnessGraphStore, edge: HarnessEdge) -> str:
    if edge.kind != "CO_CHANGED_WITH":
        return "created" if store.upsert_edge(edge) else "skipped"
    return _apply_cochange_edge(store, edge)


def _apply_cochange_edge(store: HarnessGraphStore, edge: HarnessEdge) -> str:
    existing = store.get_edge(edge.source_id, edge.target_id, edge.kind)
    occurrence_id = str(edge.metadata.get("occurrence_id") or "")
    commit_id = str(edge.metadata.get("commit_id") or "")
    work_window_id = str(edge.metadata.get("work_window_id") or "")
    if existing is None:
        normalized = _cochange_edge_with_metadata(
            store=store,
            edge=edge,
            cochange_count=1,
            occurrence_ids=(occurrence_id,) if occurrence_id else (),
            commit_ids=(commit_id,) if commit_id else (),
            work_window_ids=(work_window_id,) if work_window_id else (),
        )
        return "created" if store.upsert_edge(normalized) else "skipped"

    occurrence_ids = tuple(str(item) for item in existing.metadata.get("occurrence_ids", ()))
    if occurrence_id and occurrence_id in occurrence_ids:
        return "skipped"
    commit_ids = _append_unique_tuple(
        tuple(str(item) for item in existing.metadata.get("commit_ids", ())),
        commit_id,
    )
    work_window_ids = _append_unique_tuple(
        tuple(str(item) for item in existing.metadata.get("work_window_ids", ())),
        work_window_id,
    )
    updated_occurrences = _append_unique_tuple(occurrence_ids, occurrence_id)
    current_count = int(existing.metadata.get("cochange_count") or len(occurrence_ids) or 1)
    updated = _cochange_edge_with_metadata(
        store=store,
        edge=existing,
        cochange_count=current_count + 1,
        occurrence_ids=updated_occurrences,
        commit_ids=commit_ids,
        work_window_ids=work_window_ids,
    )
    store.replace_edge(updated)
    return "updated"


def _cochange_edge_with_metadata(
    *,
    store: HarnessGraphStore,
    edge: HarnessEdge,
    cochange_count: int,
    occurrence_ids: tuple[str, ...],
    commit_ids: tuple[str, ...],
    work_window_ids: tuple[str, ...],
) -> HarnessEdge:
    confidence = round(float(edge.confidence or 0.0), 2)
    source_changed_count = _entity_changed_count(store, edge.source_id)
    target_changed_count = _entity_changed_count(store, edge.target_id)
    either_changed_count = max(1, source_changed_count + target_changed_count - cochange_count)
    jaccard = round(cochange_count / either_changed_count, 4)
    score_components = {
        "cochange_jaccard": jaccard,
        "recency_score": 0.0,
        "validation_score": 0.0,
        "reason_quality_score": 0.0,
    }
    stored_strength = _cochange_strength(score_components)
    metadata = dict(edge.metadata)
    metadata.update(
        {
            "cochange_count": cochange_count,
            "source_changed_count": source_changed_count,
            "target_changed_count": target_changed_count,
            "either_changed_count": either_changed_count,
            "jaccard": jaccard,
            "score_components": score_components,
            "occurrence_ids": list(occurrence_ids),
            "commit_ids": list(commit_ids),
            "work_window_ids": list(work_window_ids),
            "stored_strength": stored_strength,
        }
    )
    metadata.pop("cochange_count_delta", None)
    metadata.pop("occurrence_id", None)
    metadata.pop("commit_id", None)
    metadata.pop("work_window_id", None)
    return HarnessEdge(
        source_id=edge.source_id,
        target_id=edge.target_id,
        kind=edge.kind,
        weight=stored_strength,
        confidence=confidence,
        metadata=metadata,
    )


def _update_changed_entity_stats(store: HarnessGraphStore, delta: GraphUpdateDelta) -> tuple[str, ...]:
    changed_entities = _changed_entity_ids(delta)
    updated: list[str] = []
    for entity_id in changed_entities:
        node = store.get_node(entity_id)
        if node is None:
            continue
        stats = dict(node.metadata.get("change_stats") or {})
        commit_ids = tuple(str(item) for item in stats.get("changed_commit_ids", ()))
        if delta.commit_id in commit_ids:
            continue
        next_commit_ids = (*commit_ids, delta.commit_id)
        stats.update(
            {
                "total_changed_count": len(next_commit_ids),
                "changed_commit_ids": list(next_commit_ids),
                "last_changed_commit": delta.commit_id,
            }
        )
        metadata = dict(node.metadata)
        metadata["change_stats"] = stats
        store.replace_node(
            HarnessNode(
                id=node.id,
                kind=node.kind,
                label=node.label,
                repo_id=node.repo_id,
                status=node.status,
                summary=node.summary,
                metadata=metadata,
            )
        )
        updated.append(node.id)
    return tuple(updated)


def _changed_entity_ids(delta: GraphUpdateDelta) -> tuple[str, ...]:
    entity_ids = {
        str(node.metadata.get("entity_id"))
        for node in delta.created_nodes
        if node.kind in {"FileVersion", "SymbolVersion", "CodeRegionVersion"} and node.metadata.get("entity_id")
    }
    return tuple(sorted(entity_ids))


def _entity_changed_count(store: HarnessGraphStore, node_id: str) -> int:
    node = store.get_node(node_id)
    if node is None:
        return 1
    stats = dict(node.metadata.get("change_stats") or {})
    return int(stats.get("total_changed_count") or 1)


def _cochange_strength(score_components: dict[str, float]) -> float:
    return round(
        min(
            1.0,
            (0.45 * score_components["cochange_jaccard"])
            + (0.20 * score_components["recency_score"])
            + (0.20 * score_components["validation_score"])
            + (0.15 * score_components["reason_quality_score"]),
        ),
        2,
    )


def _append_unique_tuple(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    if not value or value in values:
        return values
    return (*values, value)


def _edge_key(edge: HarnessEdge) -> EdgeKey:
    return (edge.source_id, edge.target_id, edge.kind)


__all__ = ["GraphDeltaApplyResult", "apply_graph_update_delta"]
