from __future__ import annotations

from pathlib import Path
from typing import Any

from .stage_artifacts import _read_json
from .stage_artifacts import _stage_output


def _central_merge_quality_result(artifact_dir: Path) -> Any:
    plan_result = _read_json(_stage_output(artifact_dir, "central_version_merge"))
    merge_result = artifact_dir / "central_version_merge" / "merge_result.json"
    if merge_result.exists():
        applied = _read_json(merge_result)
        if not isinstance(plan_result, dict) or not isinstance(applied, dict):
            return applied
        current_plan_id = str(plan_result.get("plan_id") or "")
        applied_plan_id = str(applied.get("plan_id") or "")
        if current_plan_id and applied_plan_id == current_plan_id:
            return applied
        return {
            **plan_result,
            "stale_merge_result": {
                "plan_id": applied_plan_id,
                "status": str(applied.get("status") or ""),
                "mode": str(applied.get("mode") or ""),
                "graph_commit_id": str((applied.get("graph_commit") if isinstance(applied.get("graph_commit"), dict) else {}).get("graph_commit_id") or ""),
            },
        }
    return plan_result


def _quality_issues(
    *,
    central_result: dict[str, Any],
    retrieval_result: dict[str, Any],
    embedding_result: dict[str, Any],
    faiss_result: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    central_status = str(central_result.get("status") or "")
    central_mode = str(central_result.get("mode") or "")
    if central_status != "applied" or central_mode != "apply_exact_atoms":
        issues.append(
            {
                "code": "central_merge_not_applied",
                "message": "central_version_merge produced a dry-run plan, not applied central memory",
                "status": central_status,
                "mode": central_mode,
                "plan_id": central_result.get("plan_id") or "",
            }
        )
    if central_result.get("stale_merge_result"):
        issues.append(
            {
                "code": "central_merge_result_stale",
                "message": "central_version_merge has a stale merge_result.json for a different plan",
                "plan_id": central_result.get("plan_id") or "",
                "stale_merge_result": central_result.get("stale_merge_result"),
            }
        )
    if central_status == "applied" and str(central_result.get("input_source") or "") != "curated_graph_manifest":
        issues.append(
            {
                "code": "central_consumed_non_curated_input",
                "message": "central_version_merge did not apply from curated_graph_manifest",
                "input_source": central_result.get("input_source") or "",
            }
        )
    if central_status == "applied" and not str(central_result.get("curated_input_hash") or ""):
        issues.append({"code": "central_missing_curated_input_hash", "message": "central apply lacks curated input hash"})
    if central_result.get("existing_atom_scan_error"):
        issues.append(
            {
                "code": "central_atom_scan_failed",
                "message": "central_version_merge could not scan existing canonical atoms",
                "error": central_result.get("existing_atom_scan_error"),
            }
        )

    doc_count = int(retrieval_result.get("doc_count") or 0)
    if doc_count <= 0:
        issues.append({"code": "retrieval_docs_empty", "message": "retrieval_docs produced no documents"})
    retrieval_source = str(retrieval_result.get("retrieval_source") or "")
    if retrieval_source in {"compact_manifest_fallback", "curated_graph_manifest_missing"}:
        issues.append(
            {
                "code": "retrieval_fallback_or_missing_curated",
                "message": "retrieval_docs did not use curated product input",
                "retrieval_source": retrieval_source,
            }
        )
    if doc_count > 0 and not str(retrieval_result.get("active_projection_id") or ""):
        issues.append({"code": "active_projection_missing", "message": "retrieval docs exist but no active projection was recorded"})
    activation_gate = retrieval_result.get("activation_gate") if isinstance(retrieval_result.get("activation_gate"), dict) else {}
    if doc_count > 0 and activation_gate and activation_gate.get("passed") is not True:
        issues.append(
            {
                "code": "retrieval_projection_activation_gate_failed",
                "message": "retrieval projection failed semantic activation gates",
                "blocking_failures": activation_gate.get("blocking_failures") or [],
            }
        )

    total_docs = int(embedding_result.get("total_docs") or doc_count or 0)
    embedded = int(embedding_result.get("embedded") or 0)
    already = int(embedding_result.get("already_embedded") or 0)
    covered = embedded + already
    if total_docs and covered < total_docs:
        issues.append(
            {
                "code": "embedding_coverage_partial",
                "message": "not all retrieval documents have active embeddings",
                "covered_docs": covered,
                "total_docs": total_docs,
                "limit_hit": bool(embedding_result.get("limit_hit")),
            }
        )

    faiss_status = str(faiss_result.get("status") or "")
    faiss_items = int(faiss_result.get("item_count") or 0)
    if total_docs and faiss_items < total_docs:
        issues.append(
            {
                "code": "faiss_coverage_partial",
                "message": "FAISS cache does not cover all retrieval documents",
                "item_count": faiss_items,
                "total_docs": total_docs,
                "status": faiss_status,
            }
        )
    if total_docs and faiss_items > total_docs:
        issues.append(
            {
                "code": "faiss_coverage_stale",
                "message": "FAISS cache includes vectors outside active retrieval document coverage",
                "item_count": faiss_items,
                "total_docs": total_docs,
                "status": faiss_status,
            }
        )
    return issues


def _quality_readiness(
    *,
    issues: list[dict[str, Any]],
    central_result: dict[str, Any],
    retrieval_result: dict[str, Any],
    embedding_result: dict[str, Any],
    faiss_result: dict[str, Any],
) -> dict[str, bool]:
    issue_codes = {str(issue.get("code") or "") for issue in issues}
    doc_count = int(retrieval_result.get("doc_count") or 0)
    total_docs = int(embedding_result.get("total_docs") or doc_count or 0)
    embedded = int(embedding_result.get("embedded") or 0)
    already = int(embedding_result.get("already_embedded") or 0)
    covered = embedded + already
    faiss_items = int(faiss_result.get("item_count") or 0)
    central_memory_ready = (
        str(central_result.get("status") or "") == "applied"
        and str(central_result.get("mode") or "") == "apply_exact_atoms"
        and str(central_result.get("input_source") or "") == "curated_graph_manifest"
        and bool(str(central_result.get("curated_input_hash") or ""))
    )
    lexical_retrieval_ready = (
        doc_count > 0
        and str(retrieval_result.get("retrieval_source") or "") in {"curated_graph_manifest", "central_active_graph_view"}
        and bool(str(retrieval_result.get("active_projection_id") or ""))
    )
    vector_retrieval_ready = bool(total_docs) and covered >= total_docs and faiss_items == total_docs
    answer_trace_ready = central_memory_ready and lexical_retrieval_ready
    product_ready = central_memory_ready and lexical_retrieval_ready and vector_retrieval_ready and answer_trace_ready and not issue_codes
    return {
        "mechanical_complete": True,
        "lexical_retrieval_ready": lexical_retrieval_ready,
        "vector_retrieval_ready": vector_retrieval_ready,
        "central_memory_ready": central_memory_ready,
        "answer_trace_ready": answer_trace_ready,
        "product_ready": product_ready,
    }
