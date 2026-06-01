from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....domain.versioning.models import utc_now
from ....infrastructure.kuzu.central_graph import repo_central_graph_path as _repo_central_graph_path
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from .constants import APPLY_ATOM_KINDS
from .errors import CentralMergeApplyError


def repo_central_graph_path(settings: Settings, repo_id: str) -> Path:
    """Compatibility wrapper for the infrastructure Kuzu central graph path."""

    return _repo_central_graph_path(settings, repo_id)


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


__all__ = [
    "_apply_summary",
    "_validate_product_plan_input",
    "_write_merge_result_artifact",
    "repo_central_graph_path",
]
