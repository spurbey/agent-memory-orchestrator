from __future__ import annotations

import uuid
from typing import Any

from ....core.config import Settings
from ....domain.versioning.models import CENTRAL_MERGE_PLAN_VERSION
from ....domain.versioning.models import utc_now
from ....infrastructure.kuzu import GraphStore
from ....infrastructure.kuzu import KuzuGraphStore
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from .artifacts import _apply_summary
from .artifacts import _validate_product_plan_input
from .artifacts import _write_merge_result_artifact
from .artifacts import repo_central_graph_path
from .constants import APPLIER_VERSION
from .constants import APPLY_ATOM_KINDS
from .constants import EXACT_APPLY_ATOM_KINDS
from .constants import REVIEW_APPLY_ATOM_KINDS
from .errors import CentralMergeApplyError
from .graph_writes import _write_exact_atoms
from .graph_writes import _write_graph_view_node
from .ids import _dedupe


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


__all__ = [
    "APPLIER_VERSION",
    "APPLY_ATOM_KINDS",
    "EXACT_APPLY_ATOM_KINDS",
    "REVIEW_APPLY_ATOM_KINDS",
    "CentralMergeApplyError",
    "apply_merge_plan",
    "repo_central_graph_path",
]
