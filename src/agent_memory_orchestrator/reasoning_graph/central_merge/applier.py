from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...graph.store import GraphEdge
from ...graph.store import GraphNode
from ...graph.store import GraphStore
from ...graph.store import KuzuGraphStore
from ...domain.versioning.merge_relations import CONFLICTS_WITH
from ...domain.versioning.merge_relations import DECISION_REVIEW_RELATIONS
from ...domain.versioning.merge_relations import DERIVED_FROM_SESSION_NODE
from ...domain.versioning.merge_relations import DUPLICATE_OF
from ...domain.versioning.merge_relations import GRAPH_VIEW_AT
from ...domain.versioning.merge_relations import REFINES
from ...domain.versioning.merge_relations import RELATED_REVIEW
from ...domain.versioning.merge_relations import STATUS_CHANGED
from ...domain.versioning.merge_relations import SUPERSEDES
from ...domain.versioning.merge_relations import VERSION_OF
from ..jobs.store import ProductionSessionJobStore
from ..jobs.store import graph_view_id
from ...domain.versioning.models import CENTRAL_MERGE_PLAN_VERSION
from ...domain.versioning.models import utc_now
from ...domain.versioning.status import STATUS_ACTIVE
from ...domain.versioning.status import STATUS_APPLIED
from ...domain.versioning.status import STATUS_CONTESTED
from ...domain.versioning.status import STATUS_REFINED
from ...domain.versioning.status import STATUS_REVIEW
from ...domain.versioning.status import STATUS_SUPERSEDED
from ...domain.versioning.status import choose_preferred_status


EXACT_APPLY_ATOM_KINDS = frozenset({"commit", "file"})
REVIEW_APPLY_ATOM_KINDS = frozenset({"decision", "problem"})
APPLY_ATOM_KINDS = EXACT_APPLY_ATOM_KINDS | REVIEW_APPLY_ATOM_KINDS
APPLIER_VERSION = "central-commit-file-decision-status-applier-v1"


class CentralMergeApplyError(RuntimeError):
    pass


def apply_merge_plan(
    *,
    settings: Settings,
    plan_id: str,
    store: ProductionSessionJobStore | None = None,
    graph_store: GraphStore | None = None,
    branch: str = "main",
    mode: str = "active",
    lock_owner: str | None = None,
) -> dict[str, Any]:
    """Apply safe central versions for a dry-run merge plan.

    Commit/file versions are answer-grade exact identities. Decision/problem
    versions are review-state only, so relation edges can be inspected without
    mutating active/refined/superseded truth.
    """

    close_store = store is None
    close_graph = False
    owned_store = store or ProductionSessionJobStore(settings)
    owned_graph = graph_store
    owner = lock_owner or f"central-merge:{uuid.uuid4().hex}"
    try:
        plan_row = owned_store.get_central_merge_plan(plan_id)
        if plan_row is None:
            raise CentralMergeApplyError(f"unknown_central_merge_plan:{plan_id}")
        plan = plan_row.get("plan") if isinstance(plan_row.get("plan"), dict) else {}
        if not plan:
            raise CentralMergeApplyError(f"invalid_central_merge_plan:{plan_id}")

        graph_commit = plan.get("graph_commit_preview") if isinstance(plan.get("graph_commit_preview"), dict) else {}
        graph_commit_id = str(graph_commit.get("graph_commit_id") or "")
        if not graph_commit_id:
            raise CentralMergeApplyError(f"missing_graph_commit_preview:{plan_id}")

        repo_id = str(plan.get("repo_id") or "")
        _validate_product_plan_input(plan)
        if owned_graph is None:
            owned_graph = KuzuGraphStore(repo_central_graph_path(settings, repo_id))
            close_graph = True
        apply_summary = _apply_summary(plan)
        current_view = owned_store.ensure_graph_view(repo_id=repo_id, branch=branch, mode=mode)
        current_head = str(current_view.get("graph_commit_id") or "")
        expected_parent = str(plan.get("parent_graph_commit_id") or "")
        reapplies_applied_head = str(plan_row.get("status") or "") == "applied" and current_head == graph_commit_id
        if not reapplies_applied_head and current_head != expected_parent:
            diagnostics = {
                "reason": "replan_required",
                "current_head": current_head,
                "expected_parent_graph_commit_id": expected_parent,
            }
            owned_store.update_central_merge_plan_status(plan_id=plan_id, status="failed_recoverable", diagnostics=diagnostics)
            return {"ok": False, "plan_id": plan_id, "status": "failed_recoverable", "error": diagnostics}

        lock_expected_parent = current_head if reapplies_applied_head else expected_parent
        if not owned_store.acquire_central_merge_lock(
            repo_id=repo_id,
            branch=branch,
            owner=owner,
            expected_parent_graph_commit_id=lock_expected_parent,
            lease_seconds=300,
        ):
            diagnostics = {"reason": "central_merge_lock_unavailable", "branch": branch}
            owned_store.update_central_merge_plan_status(plan_id=plan_id, status="failed_recoverable", diagnostics=diagnostics)
            return {"ok": False, "plan_id": plan_id, "status": "failed_recoverable", "error": diagnostics}

        try:
            owned_store.update_central_merge_plan_status(plan_id=plan_id, status="applying", mode="apply_exact_atoms")
            owned_graph.init_schema()
            added_nodes, added_edges, status_updates = _write_exact_atoms(
                graph_store=owned_graph,
                plan=plan,
                graph_commit_id=graph_commit_id,
                branch=branch,
                mode=mode,
            )
            view_nodes, view_edges = _write_graph_view_node(
                graph_store=owned_graph,
                plan=plan,
                graph_commit_id=graph_commit_id,
                branch=branch,
                mode=mode,
            )
            added_nodes = _dedupe([*added_nodes, *view_nodes])
            added_edges = _dedupe([*added_edges, *view_edges])
            graph_commit_row = owned_store.record_applied_graph_commit(
                graph_commit_id=graph_commit_id,
                plan_id=plan_id,
                job_id=str(plan.get("job_id") or ""),
                repo_id=repo_id,
                branch=branch,
                parent_graph_commit_id=expected_parent,
                pipeline_version=str(plan.get("pipeline_version") or ""),
                graph_schema_version=str(plan.get("graph_schema_version") or ""),
                algorithm_versions={
                    "central_merge_plan": str(plan.get("plan_version") or CENTRAL_MERGE_PLAN_VERSION),
                    "central_merge_applier": APPLIER_VERSION,
                },
                added_nodes=added_nodes,
                added_edges=added_edges,
                diagnostics={
                    "apply_scope": apply_summary["apply_scope"],
                    "applied_atom_counts": apply_summary["applied_atom_counts"],
                    "applied_version_counts": apply_summary["applied_version_counts"],
                    "deferred_atom_counts": apply_summary["deferred_atom_counts"],
                    "review_relation_edge_count": apply_summary["review_relation_edge_count"],
                    "status_update_count": len(status_updates),
                    "status_updates": status_updates,
                },
            )
            graph_view = owned_store.update_graph_view_head(
                repo_id=repo_id,
                branch=branch,
                mode=mode,
                graph_commit_id=graph_commit_id,
                metadata={"merge_plan_id": plan_id, "apply_scope": apply_summary["apply_scope"]},
            )
            updated_plan = owned_store.update_central_merge_plan_status(
                plan_id=plan_id,
                status="applied",
                mode="apply_exact_atoms",
                diagnostics={
                    "graph_commit_id": graph_commit_id,
                    "added_node_count": len(added_nodes),
                    "added_edge_count": len(added_edges),
                    **apply_summary,
                    "status_update_count": len(status_updates),
                    "status_updates": status_updates,
                },
            )
            result = {
                "ok": True,
                "plan_id": plan_id,
                "status": "applied",
                "mode": "apply_exact_atoms",
                "job_id": str(plan.get("job_id") or ""),
                "session_id": str(plan.get("session_id") or ""),
                "repo_id": str(plan.get("repo_id") or ""),
                "branch": branch,
                "view_mode": mode,
                "graph_commit_id": graph_commit_id,
                "graph_view_id": str(graph_view.get("view_id") or ""),
                "graph_commit": graph_commit_row,
                "graph_view": graph_view,
                "added_node_count": len(added_nodes),
                "added_edge_count": len(added_edges),
                "added_nodes": added_nodes,
                "added_edges": added_edges,
                "status_updates": status_updates,
                "status_update_count": len(status_updates),
                "input_source": str(plan.get("input_source") or ""),
                "curated_input_hash": str(plan.get("curated_input_hash") or ""),
                "trace_input_hash": str(plan.get("trace_input_hash") or ""),
                "central_graph_path": str(repo_central_graph_path(settings, repo_id)),
                **apply_summary,
                "idempotent": reapplies_applied_head,
                "plan_status": updated_plan.get("status", "applied"),
                "applied_at": utc_now(),
            }
            artifact = _write_merge_result_artifact(store=owned_store, plan=plan, result=result)
            if artifact:
                result["result_artifact"] = artifact
            return result
        except Exception as exc:
            diagnostics = {"reason": "central_merge_apply_failed", "error": str(exc)}
            owned_store.update_central_merge_plan_status(plan_id=plan_id, status="failed_partial", diagnostics=diagnostics)
            raise
        finally:
            owned_store.release_central_merge_lock(repo_id=repo_id, branch=branch, owner=owner)
    finally:
        if close_graph and owned_graph is not None:
            owned_graph.close()
        if close_store:
            owned_store.close()


def repo_central_graph_path(settings: Settings, repo_id: str) -> Path:
    """Return the repo-scoped canonical graph path.

    Session/debug graphs can become large trace stores. Central merge writes
    durable canonical atoms to a repo-scoped graph so legacy trace bloat cannot
    prevent applying the active GraphView.
    """

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(repo_id or "unknown")).strip("._-") or "unknown"
    return settings.home / ".graph" / "central" / safe / "central.kuzu"


def _write_merge_result_artifact(*, store: ProductionSessionJobStore, plan: dict[str, Any], result: dict[str, Any]) -> str:
    job_id = str(plan.get("job_id") or result.get("job_id") or "")
    if not job_id:
        return ""
    job = store.get_job(job_id)
    raw_artifact_dir = str((job or {}).get("artifact_dir") or "")
    if not raw_artifact_dir:
        return ""
    artifact_dir = Path(raw_artifact_dir)
    target_dir = artifact_dir / "central_version_merge"
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "merge_result.json"
        payload = {
            "result_version": "central-merge-apply-result-v1",
            "plan_id": result.get("plan_id", ""),
            "job_id": job_id,
            "session_id": result.get("session_id", ""),
            "repo_id": result.get("repo_id", ""),
            "status": result.get("status", ""),
            "mode": result.get("mode", ""),
            "branch": result.get("branch", ""),
            "view_mode": result.get("view_mode", ""),
            "graph_commit_id": result.get("graph_commit_id", ""),
            "graph_view_id": result.get("graph_view_id", ""),
            "graph_commit": result.get("graph_commit", {}),
            "graph_view": result.get("graph_view", {}),
            "added_node_count": result.get("added_node_count", 0),
            "added_edge_count": result.get("added_edge_count", 0),
            "added_nodes": result.get("added_nodes", []),
            "added_edges": result.get("added_edges", []),
            "applied_atom_counts": result.get("applied_atom_counts", {}),
            "applied_version_counts": result.get("applied_version_counts", {}),
            "deferred_atom_counts": result.get("deferred_atom_counts", {}),
            "review_relation_edge_count": result.get("review_relation_edge_count", 0),
            "status_update_count": result.get("status_update_count", 0),
            "status_updates": result.get("status_updates", []),
            "idempotent": result.get("idempotent", False),
            "applied_at": result.get("applied_at", utc_now()),
            "apply_scope": result.get("apply_scope", ["commit", "file", "knowledge_version", "graph_commit", "graph_view"]),
            "input_source": result.get("input_source", ""),
            "curated_input_hash": result.get("curated_input_hash", ""),
            "trace_input_hash": result.get("trace_input_hash", ""),
            "result_artifact": str(target),
        }
        target.write_text(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        return str(target)
    except OSError as exc:
        store.update_central_merge_plan_status(
            plan_id=str(result.get("plan_id") or ""),
            status=str(result.get("status") or "applied"),
            mode=str(result.get("mode") or "apply_exact_atoms"),
            diagnostics={"merge_result_artifact_error": f"{type(exc).__name__}:{exc}"},
        )
        return ""


def _validate_product_plan_input(plan: dict[str, Any]) -> None:
    if str(plan.get("input_source") or "") != "curated_graph_manifest":
        raise CentralMergeApplyError("central_merge_plan_input_is_not_curated")
    if not str(plan.get("curated_input_hash") or ""):
        raise CentralMergeApplyError("central_merge_plan_missing_curated_input_hash")


def _apply_summary(plan: dict[str, Any]) -> dict[str, Any]:
    atoms = [atom for atom in plan.get("new_atoms", []) if isinstance(atom, dict)]
    versions = [version for version in plan.get("new_versions", []) if isinstance(version, dict)]
    return {
        "apply_scope": ["commit", "file", "decision_review", "problem_review", "knowledge_version", "graph_commit", "graph_view"],
        "applied_atom_counts": _kind_counts(atoms, include=APPLY_ATOM_KINDS),
        "applied_version_counts": _kind_counts(versions, include=APPLY_ATOM_KINDS),
        "deferred_atom_counts": {
            **_kind_counts(atoms, include={"symbol", "code_region", "decision", "problem"}),
            "decision": 0,
            "problem": 0,
        },
        "review_relation_edge_count": len([edge for edge in plan.get("version_edges", []) if isinstance(edge, dict)]),
    }


def _kind_counts(items: list[dict[str, Any]], *, include: set[str] | frozenset[str]) -> dict[str, int]:
    counts = {kind: 0 for kind in sorted(include)}
    for item in items:
        kind = str(item.get("atom_kind") or "")
        if kind in counts:
            counts[kind] += 1
    return counts


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
            source_app=str(node.get("source_app") or "v2-central-merge"),
            evidence_id=str(node.get("evidence_id") or ""),
            commit_id=str(node.get("commit_id") or ""),
            created_at=str(node.get("created_at") or ""),
            updated_at=utc_now(),
            metadata=metadata,
        )
    )


def _review_candidate_count(plan: dict[str, Any], kinds: set[str]) -> int:
    candidates = plan.get("review_candidates") if isinstance(plan.get("review_candidates"), list) else []
    count = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        candidate_kind = str(candidate.get("candidate_kind") or candidate.get("frame_kind") or candidate.get("atom_kind") or "").lower()
        if candidate_kind in kinds:
            count += 1
    return count


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
                source_app="v2-central-merge",
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
                source_app="v2-central-merge",
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
            source_app="v2-central-merge",
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
            source_app="v2-central-merge",
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


def _edge_id(kind: str, source_id: str, target_id: str, graph_commit_id: str) -> str:
    return f"edge:{hashlib.sha256(f'{kind}|{source_id}|{target_id}|{graph_commit_id}'.encode('utf-8')).hexdigest()[:32]}"


def _idempotency_key(item_type: str, item_id: str, graph_commit_id: str) -> str:
    return hashlib.sha256(f"{item_type}|{item_id}|{graph_commit_id}".encode("utf-8")).hexdigest()


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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
