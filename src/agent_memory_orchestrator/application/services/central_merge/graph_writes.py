from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....domain.versioning.graph_views import graph_view_id
from ....domain.versioning.merge_relations import DECISION_REVIEW_RELATIONS
from ....domain.versioning.merge_relations import DERIVED_FROM_SESSION_NODE
from ....domain.versioning.merge_relations import GRAPH_VIEW_AT
from ....domain.versioning.merge_relations import VERSION_OF
from ....domain.versioning.models import CENTRAL_MERGE_PLAN_VERSION
from ....domain.versioning.models import utc_now
from ....domain.versioning.status import STATUS_ACTIVE
from ....domain.versioning.status import STATUS_APPLIED
from ....domain.versioning.status import STATUS_REVIEW
from ....infrastructure.kuzu import GraphEdge
from ....infrastructure.kuzu import GraphNode
from ....infrastructure.kuzu import GraphStore
from .constants import APPLIER_VERSION
from .constants import APPLY_ATOM_KINDS
from .constants import REVIEW_APPLY_ATOM_KINDS
from .ids import _dedupe
from .ids import _edge_id
from .ids import _idempotency_key
from .status import _write_decision_status_transitions


def _write_exact_atoms(
    *,
    graph_store: GraphStore,
    plan: dict[str, Any],
    graph_commit_id: str,
    branch: str,
    mode: str,
) -> tuple[list[str], list[str], list[dict[str, str]]]:
    now = utc_now()
    base = _base_metadata(plan=plan, graph_commit_id=graph_commit_id)
    atoms = [atom for atom in plan.get("new_atoms", []) if isinstance(atom, dict) and atom.get("atom_kind") in APPLY_ATOM_KINDS]
    matched_atoms = [
        atom for atom in plan.get("matched_atoms", []) if isinstance(atom, dict) and atom.get("atom_kind") in APPLY_ATOM_KINDS
    ]
    versions = [
        version
        for version in plan.get("new_versions", [])
        if isinstance(version, dict) and version.get("atom_kind") in APPLY_ATOM_KINDS
    ]
    atom_ids = {str(atom.get("atom_id") or "") for atom in atoms}
    atom_ids.update(str(atom.get("matched_atom_id") or atom.get("atom_id") or "") for atom in matched_atoms)
    resolve_source_id = _source_id_resolver(graph_store)
    added_nodes: list[str] = []
    added_edges: list[str] = []

    for atom in atoms:
        atom_id = str(atom.get("atom_id") or "")
        if not atom_id:
            continue
        atom_kind = str(atom.get("atom_kind") or "")
        graph_store.upsert_node(
            GraphNode(
                id=atom_id,
                kind="KnowledgeAtom",
                label=_label_for_atom(atom),
                summary=str(atom.get("canonical_key") or ""),
                status=STATUS_REVIEW if atom_kind in REVIEW_APPLY_ATOM_KINDS else STATUS_ACTIVE,
                scope="central",
                session_id=str(plan.get("session_id") or ""),
                source_app="production-central-merge",
                created_at=now,
                metadata={
                    **base,
                    "atom_kind": atom_kind,
                    "repo_id": str(atom.get("repo_id") or plan.get("repo_id") or ""),
                    "repo_path": str(atom.get("repo_path") or plan.get("repo_path") or ""),
                    "canonical_key": str(atom.get("canonical_key") or ""),
                    "canonical_key_version": int(atom.get("canonical_key_version") or 1),
                    "source_node_ids": atom.get("source_node_ids") if isinstance(atom.get("source_node_ids"), list) else [],
                    "idempotency_key": _idempotency_key("node", atom_id, graph_commit_id),
                },
            )
        )
        added_nodes.append(atom_id)

    for version in versions:
        version_id = str(version.get("version_id") or "")
        atom_id = str(version.get("atom_id") or "")
        if not version_id or atom_id not in atom_ids:
            continue
        graph_store.upsert_node(
            GraphNode(
                id=version_id,
                kind="KnowledgeVersion",
                label=_label_for_version(version),
                summary=str(version.get("metadata", {}).get("canonical_key") if isinstance(version.get("metadata"), dict) else ""),
                status=str(version.get("status") or STATUS_ACTIVE),
                scope="central",
                session_id=str(version.get("session_id") or plan.get("session_id") or ""),
                source_app="production-central-merge",
                created_at=now,
                metadata={
                    **base,
                    "atom_id": atom_id,
                    "atom_kind": str(version.get("atom_kind") or ""),
                    "repo_id": str(plan.get("repo_id") or ""),
                    "status": str(version.get("status") or STATUS_ACTIVE),
                    "source_node_ids": version.get("source_node_ids") if isinstance(version.get("source_node_ids"), list) else [],
                    "version_metadata": version.get("metadata") if isinstance(version.get("metadata"), dict) else {},
                    "idempotency_key": _idempotency_key("node", version_id, graph_commit_id),
                },
            )
        )
        added_nodes.append(version_id)
        edge_id = _edge_id(VERSION_OF, version_id, atom_id, graph_commit_id)
        graph_store.upsert_edge(
            GraphEdge(
                id=edge_id,
                source_id=version_id,
                target_id=atom_id,
                kind=VERSION_OF,
                confidence=1.0,
                created_at=now,
                metadata={**base, "idempotency_key": _idempotency_key("edge", edge_id, graph_commit_id)},
            )
        )
        added_edges.append(edge_id)
        for source_node_id in version.get("source_node_ids", []) if isinstance(version.get("source_node_ids"), list) else []:
            source_id = resolve_source_id(str(source_node_id or ""))
            if not source_id:
                continue
            derived_edge_id = _edge_id(DERIVED_FROM_SESSION_NODE, version_id, source_id, graph_commit_id)
            graph_store.upsert_edge(
                GraphEdge(
                    id=derived_edge_id,
                    source_id=version_id,
                    target_id=source_id,
                    kind=DERIVED_FROM_SESSION_NODE,
                    confidence=1.0,
                    created_at=now,
                    metadata={**base, "idempotency_key": _idempotency_key("edge", derived_edge_id, graph_commit_id)},
                )
            )
            added_edges.append(derived_edge_id)

    version_ids = {str(version.get("version_id") or "") for version in versions}
    relation_edges: list[dict[str, Any]] = []
    for relation in plan.get("version_edges", []) if isinstance(plan.get("version_edges"), list) else []:
        if not isinstance(relation, dict):
            continue
        source_id = str(relation.get("source_id") or "")
        target_id = str(relation.get("target_id") or "")
        kind = str(relation.get("kind") or "")
        if kind not in DECISION_REVIEW_RELATIONS:
            continue
        if source_id == target_id:
            continue
        if source_id not in version_ids or (target_id not in version_ids and not target_id.startswith("kver:")):
            continue
        edge_id = str(relation.get("edge_id") or _edge_id(kind, source_id, target_id, graph_commit_id))
        metadata = relation.get("metadata") if isinstance(relation.get("metadata"), dict) else {}
        graph_store.upsert_edge(
            GraphEdge(
                id=edge_id,
                source_id=source_id,
                target_id=target_id,
                kind=kind,
                confidence=float(relation.get("confidence") or 0.0),
                created_at=now,
                metadata={**base, **metadata, "idempotency_key": _idempotency_key("edge", edge_id, graph_commit_id), "status": str(relation.get("status") or STATUS_REVIEW)},
            )
        )
        added_edges.append(edge_id)
        relation_edges.append({**relation, "edge_id": edge_id, "source_id": source_id, "target_id": target_id, "kind": kind})

    status_updates, status_edges = _write_decision_status_transitions(
        graph_store=graph_store,
        plan=plan,
        versions=versions,
        relation_edges=relation_edges,
        graph_commit_id=graph_commit_id,
        base=base,
        now=now,
    )
    added_edges.extend(status_edges)

    graph_store.upsert_node(
        GraphNode(
            id=graph_commit_id,
            kind="GraphCommit",
            label=graph_commit_id,
            summary=f"Applied central exact atoms for {plan.get('job_id', '')}",
            status=STATUS_APPLIED,
            scope="central",
            session_id=str(plan.get("session_id") or ""),
            source_app="production-central-merge",
            created_at=now,
            metadata={
                **base,
                "branch": branch,
                "parent_graph_commit_id": str(plan.get("parent_graph_commit_id") or ""),
                "added_node_count": len(added_nodes),
                "added_edge_count": len(added_edges),
                "status_update_count": len(status_updates),
                "status_updates": status_updates,
                "idempotency_key": _idempotency_key("node", graph_commit_id, graph_commit_id),
            },
        )
    )
    added_nodes.append(graph_commit_id)
    return _dedupe(added_nodes), _dedupe(added_edges), status_updates


def _write_graph_view_node(
    *,
    graph_store: GraphStore,
    plan: dict[str, Any],
    graph_commit_id: str,
    branch: str,
    mode: str,
) -> tuple[list[str], list[str]]:
    now = utc_now()
    base = _base_metadata(plan=plan, graph_commit_id=graph_commit_id)
    graph_view_id_value = graph_view_id(repo_id=str(plan.get("repo_id") or ""), branch=branch, mode=mode)
    graph_store.upsert_node(
        GraphNode(
            id=graph_view_id_value,
            kind="GraphView",
            label=f"{branch}/{mode}",
            summary=f"GraphView {branch}/{mode} at {graph_commit_id}",
            status=STATUS_ACTIVE,
            scope="central",
            session_id=str(plan.get("session_id") or ""),
            source_app="production-central-merge",
            created_at=now,
            metadata={
                **base,
                "branch": branch,
                "mode": mode,
                "graph_commit_id": graph_commit_id,
                "idempotency_key": _idempotency_key("node", graph_view_id_value, graph_commit_id),
            },
        )
    )
    view_edge_id = _edge_id(GRAPH_VIEW_AT, graph_view_id_value, graph_commit_id, graph_commit_id)
    graph_store.upsert_edge(
        GraphEdge(
            id=view_edge_id,
            source_id=graph_view_id_value,
            target_id=graph_commit_id,
            kind=GRAPH_VIEW_AT,
            confidence=1.0,
            created_at=now,
            metadata={**base, "idempotency_key": _idempotency_key("edge", view_edge_id, graph_commit_id)},
        )
    )
    return [graph_view_id_value], [view_edge_id]


def _base_metadata(*, plan: dict[str, Any], graph_commit_id: str) -> dict[str, Any]:
    return {
        "merge_plan_id": str(plan.get("plan_id") or ""),
        "graph_commit_id": graph_commit_id,
        "pipeline_version": str(plan.get("pipeline_version") or ""),
        "graph_schema_version": str(plan.get("graph_schema_version") or ""),
        "repo_id": str(plan.get("repo_id") or ""),
        "job_id": str(plan.get("job_id") or ""),
        "session_id": str(plan.get("session_id") or ""),
        "central_merge_plan_version": str(plan.get("plan_version") or CENTRAL_MERGE_PLAN_VERSION),
        "central_merge_applier_version": APPLIER_VERSION,
    }


def _label_for_atom(atom: dict[str, Any]) -> str:
    metadata = atom.get("metadata") if isinstance(atom.get("metadata"), dict) else {}
    return str(
        metadata.get("qualified_name")
        or metadata.get("file_path")
        or metadata.get("commit_sha")
        or atom.get("canonical_key")
        or atom.get("atom_id")
        or "KnowledgeAtom"
    )


def _label_for_version(version: dict[str, Any]) -> str:
    metadata = version.get("metadata") if isinstance(version.get("metadata"), dict) else {}
    return str(metadata.get("canonical_key") or version.get("version_id") or "KnowledgeVersion")



def _source_id_resolver(graph_store: GraphStore) -> Callable[[str], str]:
    ids: set[str] = set()
    try:
        ids = {str(node.get("id") or "") for node in graph_store.list_nodes(limit=100000)}
    except Exception:
        ids = set()
    suffix_matches: dict[str, str] = {}
    for node_id in ids:
        if ":" not in node_id:
            continue
        suffix = node_id.split(":", 1)[1]
        if suffix in suffix_matches and suffix_matches[suffix] != node_id:
            suffix_matches[suffix] = ""
        else:
            suffix_matches[suffix] = node_id

    def resolve(source_id: str) -> str:
        if not source_id:
            return ""
        if not ids or source_id in ids:
            return source_id
        matched = suffix_matches.get(source_id)
        return matched or source_id

    return resolve


__all__ = ["_write_exact_atoms", "_write_graph_view_node"]
