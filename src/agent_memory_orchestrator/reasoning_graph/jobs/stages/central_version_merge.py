from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...central_merge import build_dry_run_merge_plan
from ...central_merge.applier import apply_merge_plan
from ...central_merge.applier import repo_central_graph_path
from ....domain.versioning.repo_identity import resolve_repo_identity
from ..runner import StageFailed
from ..runner import StageResult
from ..runner import _product_manifest_info
from ..runner import _read_json
from ..runner import _stage_output


def run_central_version_merge_stage(runner: Any, job: dict[str, Any], artifact_dir: Path, stage_dir: Path) -> StageResult:
    del artifact_dir
    job_artifact_dir = Path(str(job["artifact_dir"]))
    session_graph_result = _read_json(_stage_output(job_artifact_dir, "kuzu_write"))
    manifest_info = _product_manifest_info(job_artifact_dir)
    manifest_path = Path(str(manifest_info["curated_manifest_path"]))
    compact_graph = _read_json(manifest_path)
    repo_id = str(job.get("repo_id") or "") or resolve_repo_identity(str(job.get("repo_path") or "")).repo_id
    active_view = runner.job_store.ensure_graph_view(repo_id=repo_id, branch="main", mode="active")
    parent_graph_commit_id = str(active_view.get("graph_commit_id") or "")
    existing_atom_scan_error = ""
    try:
        existing_atoms = runner._central_atoms_by_canonical_key(repo_id=repo_id)
        active_central_versions = runner._central_active_versions(repo_id=repo_id)
    except Exception as exc:
        existing_atoms = {}
        active_central_versions = []
        existing_atom_scan_error = f"{type(exc).__name__}: {exc}"
    historical_decision_frames = runner.job_store.list_decision_frames(
        repo_id=repo_id,
        exclude_job_id=str(job.get("job_id") or ""),
    )
    plan = build_dry_run_merge_plan(
        job={**job, "repo_id": repo_id},
        compact_graph=compact_graph if isinstance(compact_graph, dict) else {},
        parent_graph_commit_id=parent_graph_commit_id,
        existing_atoms_by_canonical_key=existing_atoms,
        active_central_versions=active_central_versions,
        historical_decision_frames=historical_decision_frames,
    )
    plan_payload = plan.as_dict()
    plan_payload["session_graph_write"] = session_graph_result if isinstance(session_graph_result, dict) else {}
    plan_payload["input_source"] = "curated_graph_manifest"
    plan_payload["curated_input_hash"] = manifest_info["curated_input_hash"]
    plan_payload["trace_input_hash"] = manifest_info["trace_input_hash"]
    plan_payload["curated_manifest_path"] = str(manifest_path)
    plan_payload["apply_scope"] = plan.metrics.get("apply_scope", [])
    plan_payload["deferred_atom_kinds"] = ["symbol", "code_region", "decision", "problem"]
    plan_payload["deferred_atom_counts"] = plan.metrics.get("deferred_atom_counts", {})
    if existing_atom_scan_error:
        plan_payload["existing_atom_scan_error"] = existing_atom_scan_error
    runner.job_store.upsert_central_merge_plan(plan_payload)
    output = stage_dir / "merge_plan.json"
    output.write_text(json.dumps(plan_payload, indent=2, ensure_ascii=False), encoding="utf-8")
    central_graph = runner.graph_store_factory(repo_central_graph_path(runner.settings, repo_id))
    try:
        apply_result = apply_merge_plan(
            settings=runner.settings,
            plan_id=plan.plan_id,
            store=runner.job_store,
            graph_store=central_graph,
            lock_owner=f"production-job:{job.get('job_id') or ''}",
        )
    finally:
        central_graph.close()
    if not apply_result.get("ok"):
        raise StageFailed("central_merge_apply_failed", apply_result)
    diagnostics = {
        "status": apply_result.get("status") or "applied",
        "mode": apply_result.get("mode") or "apply_exact_atoms",
        "plan_id": plan.plan_id,
        "plan_hash": plan.plan_hash,
        "input_graph_hash": plan.input_graph_hash,
        "repo_id": plan.repo_id,
        "repo_path": plan.repo_path,
        "graph_commit_id": apply_result.get("graph_commit_id") or "",
        "graph_view_id": apply_result.get("graph_view_id") or "",
        "result_artifact": apply_result.get("result_artifact") or "",
        "added_node_count": apply_result.get("added_node_count") or 0,
        "added_edge_count": apply_result.get("added_edge_count") or 0,
        "applied_atom_counts": apply_result.get("applied_atom_counts") or {},
        "applied_version_counts": apply_result.get("applied_version_counts") or {},
        "deferred_atom_counts": apply_result.get("deferred_atom_counts") or {},
        "status_update_count": apply_result.get("status_update_count") or 0,
        "graph_commit_preview": plan.graph_commit_preview,
        "metrics": plan.metrics,
        "review_candidate_count": len(plan.review_candidates),
        "existing_atom_scan_error": existing_atom_scan_error,
        "input_source": "curated_graph_manifest",
        "curated_input_hash": manifest_info["curated_input_hash"],
        "trace_input_hash": manifest_info["trace_input_hash"],
    }
    return StageResult(output_path=output, diagnostics=diagnostics)
