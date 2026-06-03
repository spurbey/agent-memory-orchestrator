from __future__ import annotations

from typing import Any

from ....domain.versioning.merge_relations import CONFLICTS_WITH
from ....domain.versioning.merge_relations import DUPLICATE_OF
from ....domain.versioning.merge_relations import REFINES
from ....domain.versioning.merge_relations import RELATED_REVIEW
from ....domain.versioning.merge_relations import STATUS_CHANGED
from ....domain.versioning.merge_relations import SUPERSEDES
from ....domain.versioning.models import utc_now
from ....domain.versioning.status import STATUS_ACTIVE
from ....domain.versioning.status import STATUS_CONTESTED
from ....domain.versioning.status import STATUS_REFINED
from ....domain.versioning.status import STATUS_REVIEW
from ....domain.versioning.status import STATUS_SUPERSEDED
from ....domain.versioning.status import choose_preferred_status
from ....infrastructure.kuzu import GraphEdge
from ....infrastructure.kuzu import GraphNode
from ....infrastructure.kuzu import GraphStore
from .constants import REVIEW_APPLY_ATOM_KINDS
from .ids import _edge_id
from .ids import _idempotency_key


def _write_decision_status_transitions(
    *,
    graph_store: GraphStore,
    plan: dict[str, Any],
    versions: list[dict[str, Any]],
    relation_edges: list[dict[str, Any]],
    graph_commit_id: str,
    base: dict[str, Any],
    now: str,
) -> tuple[list[dict[str, str]], list[str]]:
    """Apply conservative decision/problem status transitions.

    Review relation edges are useful, but versioning needs a current-state
    pointer. This policy only promotes accepted decisions that are not involved
    in ambiguous review relations, and only mutates older statuses when the
    relation itself carries clear duplicate/refine/supersede/conflict meaning.
    """

    decision_version_ids = {
        str(version.get("version_id") or "")
        for version in versions
        if str(version.get("atom_kind") or "") in REVIEW_APPLY_ATOM_KINDS and str(version.get("version_id") or "")
    }
    if not decision_version_ids:
        return [], []

    nodes = _nodes_by_id(graph_store.list_nodes(kinds=["KnowledgeVersion"], limit=1_000_000))
    desired: dict[str, tuple[str, str]] = {}
    relation_version_ids: set[str] = set()
    ambiguous_version_ids: set[str] = set()

    for edge in relation_edges:
        kind = str(edge.get("kind") or "")
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        confidence = float(edge.get("confidence") or 0.0)
        evidence = _relation_evidence(edge)
        if not source_id or not target_id or source_id == target_id:
            continue
        if source_id in decision_version_ids:
            relation_version_ids.add(source_id)
        if target_id in decision_version_ids:
            relation_version_ids.add(target_id)
        if kind == RELATED_REVIEW:
            ambiguous_version_ids.update(item for item in (source_id, target_id) if item in decision_version_ids)
            continue
        if not _relation_targets_central_version(edge):
            ambiguous_version_ids.update(item for item in (source_id, target_id) if item in decision_version_ids)
            continue
        if kind == DUPLICATE_OF and confidence >= 0.82:
            if _is_decision_version(nodes.get(target_id, {})):
                _choose_status(desired, target_id, STATUS_ACTIVE, "duplicate_canonical")
            continue
        if kind == REFINES and confidence >= 0.76:
            if source_id in decision_version_ids:
                _choose_status(desired, source_id, STATUS_ACTIVE, "refines_newer_version")
            if _is_decision_version(nodes.get(target_id, {})):
                _choose_status(desired, target_id, STATUS_REFINED, "refined_by_newer_version")
            continue
        if kind == SUPERSEDES and _safe_supersedes(confidence=confidence, evidence=evidence):
            if source_id in decision_version_ids:
                _choose_status(desired, source_id, STATUS_ACTIVE, "supersedes_prior_version")
            if _is_decision_version(nodes.get(target_id, {})):
                _choose_status(desired, target_id, STATUS_SUPERSEDED, "superseded_by_newer_version")
            continue
        if kind == CONFLICTS_WITH and _safe_conflict(confidence=confidence, evidence=evidence):
            if source_id in decision_version_ids:
                _choose_status(desired, source_id, STATUS_CONTESTED, "conflicts_with_review")
            if _is_decision_version(nodes.get(target_id, {})):
                _choose_status(desired, target_id, STATUS_CONTESTED, "conflicts_with_review")

    for version_id in sorted(decision_version_ids):
        if version_id not in relation_version_ids and version_id not in ambiguous_version_ids:
            _choose_status(desired, version_id, STATUS_ACTIVE, "new_decision_no_conflict")

    updates: list[dict[str, str]] = []
    edge_ids: list[str] = []
    for version_id, (new_status, reason) in sorted(desired.items()):
        node = nodes.get(version_id)
        if not node or not _is_decision_version(node):
            continue
        old_status = str(node.get("status") or (node.get("metadata") or {}).get("status") or STATUS_REVIEW)
        if old_status == new_status:
            continue
        _upsert_node_status(graph_store, node=node, status=new_status)
        edge_id = _edge_id(STATUS_CHANGED, version_id, version_id, graph_commit_id)
        graph_store.upsert_edge(
            GraphEdge(
                id=edge_id,
                source_id=version_id,
                target_id=version_id,
                kind=STATUS_CHANGED,
                confidence=1.0,
                created_at=now,
                metadata={
                    **base,
                    "old_status": old_status,
                    "new_status": new_status,
                    "reason": reason,
                    "idempotency_key": _idempotency_key("edge", edge_id, graph_commit_id),
                },
            )
        )
        updates.append({"version_id": version_id, "old_status": old_status, "new_status": new_status, "reason": reason})
        edge_ids.append(edge_id)
    return updates, edge_ids


def _nodes_by_id(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(node.get("id") or ""): node for node in nodes if str(node.get("id") or "")}


def _is_decision_version(node: dict[str, Any]) -> bool:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("kind") or node.get("node_kind") or "") == "KnowledgeVersion" and str(metadata.get("atom_kind") or "") in REVIEW_APPLY_ATOM_KINDS


def _choose_status(desired: dict[str, tuple[str, str]], version_id: str, status: str, reason: str) -> None:
    current = desired.get(version_id)
    preferred = choose_preferred_status(current, status, reason)
    if preferred != current:
        desired[version_id] = preferred


def _relation_evidence(edge: dict[str, Any]) -> dict[str, Any]:
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    score = metadata.get("score") if isinstance(metadata.get("score"), dict) else {}
    return {
        "reason": str(metadata.get("reason") or edge.get("reason") or ""),
        "lexical": float(score.get("lexical") or 0.0),
        "file_overlap": float(score.get("file_overlap") or 0.0),
        "code_entity_overlap": float(score.get("code_entity_overlap") or 0.0),
        "false_positive_risk": bool(score.get("false_positive_risk")),
        "source_scope": str(score.get("source_scope") or ""),
        "target_scope": str(score.get("target_scope") or ""),
    }


def _relation_targets_central_version(edge: dict[str, Any]) -> bool:
    metadata = edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {}
    score = metadata.get("score") if isinstance(metadata.get("score"), dict) else {}
    return str(score.get("target_scope") or "").lower() == "central"


def _safe_supersedes(*, confidence: float, evidence: dict[str, Any]) -> bool:
    if confidence >= 0.68:
        return True
    return (
        str(evidence.get("reason") or "") == "new_decision_uses_replacement_language"
        and float(evidence.get("code_entity_overlap") or 0.0) >= 0.6
        and float(evidence.get("lexical") or 0.0) >= 0.35
        and not bool(evidence.get("false_positive_risk"))
    )


def _safe_conflict(*, confidence: float, evidence: dict[str, Any]) -> bool:
    if confidence >= 0.68:
        return True
    return (
        str(evidence.get("reason") or "") == "incompatible_decision_language"
        and float(evidence.get("file_overlap") or 0.0) >= 0.6
        and float(evidence.get("lexical") or 0.0) >= 0.35
        and not bool(evidence.get("false_positive_risk"))
    )


def _upsert_node_status(graph_store: GraphStore, *, node: dict[str, Any], status: str) -> None:
    metadata = dict(node.get("metadata") if isinstance(node.get("metadata"), dict) else {})
    metadata["status"] = status
    graph_store.upsert_node(
        GraphNode(
            id=str(node.get("id") or ""),
            kind=str(node.get("kind") or node.get("node_kind") or "KnowledgeVersion"),
            label=str(node.get("label") or ""),
            summary=str(node.get("summary") or ""),
            status=status,
            scope=str(node.get("scope") or "central"),
            session_id=str(node.get("session_id") or ""),
            project_id=str(node.get("project_id") or "default"),
            source_app=str(node.get("source_app") or "production-central-merge"),
            evidence_id=str(node.get("evidence_id") or ""),
            commit_id=str(node.get("commit_id") or ""),
            created_at=str(node.get("created_at") or ""),
            updated_at=utc_now(),
            metadata=metadata,
        )
    )


__all__ = ["_write_decision_status_transitions"]
