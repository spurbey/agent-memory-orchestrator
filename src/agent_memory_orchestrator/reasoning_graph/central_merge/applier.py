from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...graph.store import GraphEdge
from ...graph.store import GraphNode
from ...graph.store import GraphStore
from ...graph.store import KuzuGraphStore
from ..jobs.store import V2SessionJobStore
from ..jobs.store import graph_view_id
from .models import CENTRAL_MERGE_PLAN_VERSION
from .models import utc_now


EXACT_APPLY_ATOM_KINDS = frozenset({"commit", "file"})
APPLIER_VERSION = "central-exact-atom-applier-v1"


class CentralMergeApplyError(RuntimeError):
    pass


def apply_merge_plan(
    *,
    settings: Settings,
    plan_id: str,
    store: V2SessionJobStore | None = None,
    graph_store: GraphStore | None = None,
    branch: str = "main",
    mode: str = "active",
    lock_owner: str | None = None,
) -> dict[str, Any]:
    """Apply deterministic central atoms for a dry-run merge plan.

    This intentionally excludes decision/problem semantic merge. Phase 4 only
    promotes exact repo-scoped identities that are safe to apply repeatedly.
    """

    close_store = store is None
    close_graph = graph_store is None
    owned_store = store or V2SessionJobStore(settings)
    owned_graph = graph_store or KuzuGraphStore(settings.graph_path)
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
            added_nodes, added_edges = _write_exact_atoms(
                graph_store=owned_graph,
                plan=plan,
                graph_commit_id=graph_commit_id,
                branch=branch,
                mode=mode,
            )
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
                "graph_commit": graph_commit_row,
                "graph_view": graph_view,
                "added_node_count": len(added_nodes),
                "added_edge_count": len(added_edges),
                "added_nodes": added_nodes,
                "added_edges": added_edges,
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
        if close_graph:
            owned_graph.close()
        if close_store:
            owned_store.close()


def _write_merge_result_artifact(*, store: V2SessionJobStore, plan: dict[str, Any], result: dict[str, Any]) -> str:
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
            "graph_commit": result.get("graph_commit", {}),
            "graph_view": result.get("graph_view", {}),
            "added_node_count": result.get("added_node_count", 0),
            "added_edge_count": result.get("added_edge_count", 0),
            "added_nodes": result.get("added_nodes", []),
            "added_edges": result.get("added_edges", []),
            "applied_atom_counts": result.get("applied_atom_counts", {}),
            "applied_version_counts": result.get("applied_version_counts", {}),
            "deferred_atom_counts": result.get("deferred_atom_counts", {}),
            "idempotent": result.get("idempotent", False),
            "applied_at": result.get("applied_at", utc_now()),
            "apply_scope": result.get("apply_scope", ["commit", "file", "knowledge_version", "graph_commit", "graph_view"]),
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
    if str(plan.get("input_source") or "") not in {"curated_graph_manifest", ""}:
        raise CentralMergeApplyError("central_merge_plan_input_is_not_curated")
    if not str(plan.get("curated_input_hash") or ""):
        raise CentralMergeApplyError("central_merge_plan_missing_curated_input_hash")


def _apply_summary(plan: dict[str, Any]) -> dict[str, Any]:
    atoms = [atom for atom in plan.get("new_atoms", []) if isinstance(atom, dict)]
    versions = [version for version in plan.get("new_versions", []) if isinstance(version, dict)]
    return {
        "apply_scope": ["commit", "file", "knowledge_version", "graph_commit", "graph_view"],
        "applied_atom_counts": _kind_counts(atoms, include=EXACT_APPLY_ATOM_KINDS),
        "applied_version_counts": _kind_counts(versions, include=EXACT_APPLY_ATOM_KINDS),
        "deferred_atom_counts": {
            **_kind_counts(atoms, include={"symbol", "code_region", "decision", "problem"}),
            "decision": _review_candidate_count(plan, {"decision"}),
            "problem": _review_candidate_count(plan, {"problem"}),
        },
    }


def _kind_counts(items: list[dict[str, Any]], *, include: set[str] | frozenset[str]) -> dict[str, int]:
    counts = {kind: 0 for kind in sorted(include)}
    for item in items:
        kind = str(item.get("atom_kind") or "")
        if kind in counts:
            counts[kind] += 1
    return counts


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
) -> tuple[list[str], list[str]]:
    now = utc_now()
    base = _base_metadata(plan=plan, graph_commit_id=graph_commit_id)
    atoms = [atom for atom in plan.get("new_atoms", []) if isinstance(atom, dict) and atom.get("atom_kind") in EXACT_APPLY_ATOM_KINDS]
    matched_atoms = [
        atom for atom in plan.get("matched_atoms", []) if isinstance(atom, dict) and atom.get("atom_kind") in EXACT_APPLY_ATOM_KINDS
    ]
    versions = [
        version
        for version in plan.get("new_versions", [])
        if isinstance(version, dict) and version.get("atom_kind") in EXACT_APPLY_ATOM_KINDS
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
        graph_store.upsert_node(
            GraphNode(
                id=atom_id,
                kind="KnowledgeAtom",
                label=_label_for_atom(atom),
                summary=str(atom.get("canonical_key") or ""),
                status="active",
                scope="central",
                session_id=str(plan.get("session_id") or ""),
                source_app="v2-central-merge",
                created_at=now,
                metadata={
                    **base,
                    "atom_kind": str(atom.get("atom_kind") or ""),
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
                status=str(version.get("status") or "active"),
                scope="central",
                session_id=str(version.get("session_id") or plan.get("session_id") or ""),
                source_app="v2-central-merge",
                created_at=now,
                metadata={
                    **base,
                    "atom_id": atom_id,
                    "atom_kind": str(version.get("atom_kind") or ""),
                    "repo_id": str(plan.get("repo_id") or ""),
                    "status": str(version.get("status") or "active"),
                    "source_node_ids": version.get("source_node_ids") if isinstance(version.get("source_node_ids"), list) else [],
                    "version_metadata": version.get("metadata") if isinstance(version.get("metadata"), dict) else {},
                    "idempotency_key": _idempotency_key("node", version_id, graph_commit_id),
                },
            )
        )
        added_nodes.append(version_id)
        edge_id = _edge_id("VERSION_OF", version_id, atom_id, graph_commit_id)
        graph_store.upsert_edge(
            GraphEdge(
                id=edge_id,
                source_id=version_id,
                target_id=atom_id,
                kind="VERSION_OF",
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
            derived_edge_id = _edge_id("DERIVED_FROM_SESSION_NODE", version_id, source_id, graph_commit_id)
            graph_store.upsert_edge(
                GraphEdge(
                    id=derived_edge_id,
                    source_id=version_id,
                    target_id=source_id,
                    kind="DERIVED_FROM_SESSION_NODE",
                    confidence=1.0,
                    created_at=now,
                    metadata={**base, "idempotency_key": _idempotency_key("edge", derived_edge_id, graph_commit_id)},
                )
            )
            added_edges.append(derived_edge_id)

    graph_store.upsert_node(
        GraphNode(
            id=graph_commit_id,
            kind="GraphCommit",
            label=graph_commit_id,
            summary=f"Applied central exact atoms for {plan.get('job_id', '')}",
            status="applied",
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
                "idempotency_key": _idempotency_key("node", graph_commit_id, graph_commit_id),
            },
        )
    )
    added_nodes.append(graph_commit_id)
    graph_view_id_value = graph_view_id(repo_id=str(plan.get("repo_id") or ""), branch=branch, mode=mode)
    graph_store.upsert_node(
        GraphNode(
            id=graph_view_id_value,
            kind="GraphView",
            label=f"{branch}/{mode}",
            summary=f"GraphView {branch}/{mode} at {graph_commit_id}",
            status="active",
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
    added_nodes.append(graph_view_id_value)
    view_edge_id = _edge_id("GRAPH_VIEW_AT", graph_view_id_value, graph_commit_id, graph_commit_id)
    graph_store.upsert_edge(
        GraphEdge(
            id=view_edge_id,
            source_id=graph_view_id_value,
            target_id=graph_commit_id,
            kind="GRAPH_VIEW_AT",
            confidence=1.0,
            created_at=now,
            metadata={**base, "idempotency_key": _idempotency_key("edge", view_edge_id, graph_commit_id)},
        )
    )
    added_edges.append(view_edge_id)
    return _dedupe(added_nodes), _dedupe(added_edges)


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
