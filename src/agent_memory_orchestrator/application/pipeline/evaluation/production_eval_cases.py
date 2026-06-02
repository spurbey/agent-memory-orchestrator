from __future__ import annotations

from typing import Any


def _cases(
    *,
    kuzu_write: dict[str, Any],
    central: dict[str, Any],
    retrieval: dict[str, Any],
    quality: dict[str, Any],
    mode: str = "baseline",
) -> list[dict[str, Any]]:
    active_projection_metadata = retrieval.get("active_projection", {}).get("metadata")
    if not isinstance(active_projection_metadata, dict):
        active_projection_metadata = {}
    active_projection_uses_curated = (
        mode == "post_apply"
        and active_projection_metadata.get("curated_manifest_exists") is True
        and active_projection_metadata.get("retrieval_source") == "curated_graph_manifest"
    )
    curated_manifest_present = bool(kuzu_write.get("curated_manifest_exists")) or active_projection_uses_curated
    cases = [
        _case(
            "curated_manifest_present",
            expected={"curated_graph_manifest": "exists"},
            actual={
                "exists": kuzu_write.get("curated_manifest_exists"),
                "path": kuzu_write.get("curated_manifest_path"),
                "active_projection_curated_manifest": active_projection_metadata.get("curated_manifest_exists"),
                "active_projection_retrieval_source": active_projection_metadata.get("retrieval_source"),
            },
            passed=curated_manifest_present,
            failures=[] if curated_manifest_present else ["curated_graph_manifest_missing"],
            reason="Production memory input must be the curated graph manifest.",
        ),
        _case(
            "central_merge_applied",
            expected={"status": "applied", "active_graph_view_head": "non_empty"},
            actual={
                "plan_status": central.get("plan_status"),
                "plan_mode": central.get("plan_mode"),
                "graph_commit_status": central.get("graph_commit_status"),
                "active_graph_view_head": central.get("active_graph_view_head"),
                "source": central.get("source"),
            },
            passed=central.get("applied") is True and bool(central.get("active_graph_view_head")),
            failures=[] if central.get("applied") and central.get("active_graph_view_head") else ["central_merge_not_applied"],
            reason="AMO repo central memory is not active until a GraphCommit backs GraphView(main, active).",
        ),
        _case(
            "retrieval_not_full_trace_dominated",
            expected={"dominant_docs": "curated_or_central"},
            actual={
                "repo_doc_count": retrieval.get("repo_doc_count"),
                "trace_doc_count": retrieval.get("trace_doc_count"),
                "curated_doc_count": retrieval.get("curated_doc_count"),
                "doc_type_counts": retrieval.get("doc_type_counts"),
            },
            passed=bool(retrieval.get("repo_doc_count")) and not retrieval.get("full_trace_dominated"),
            failures=_retrieval_projection_failures(retrieval),
            reason="Strict repo retrieval must not surface raw CodeNode/CodeHunk trace docs as the product projection.",
        ),
        _case(
            "strict_repo_excludes_legacy_docs",
            expected={"repo_id_empty_docs": "not_in_strict_repo"},
            actual={
                "legacy_doc_count": retrieval.get("legacy_doc_count"),
                "strict_repo_id": retrieval.get("repo_id"),
            },
            passed=retrieval.get("strict_repo_legacy_leak") is False,
            failures=[] if retrieval.get("strict_repo_legacy_leak") is False else ["strict_repo_legacy_leak"],
            reason="Legacy repo_id='' docs may exist, but strict repo retrieval must not use them.",
        ),
        _case(
            "vector_status_truthful",
            expected={"embedding_status": "ready_or_partial_truthful", "faiss_status": "ready_or_partial_truthful"},
            actual={"embedding": retrieval.get("embedding_coverage"), "faiss": retrieval.get("faiss")},
            passed=bool(retrieval.get("vector_status_truthful")),
            failures=[] if retrieval.get("vector_status_truthful") else ["vector_status_unavailable"],
            reason="Partial embeddings/FAISS are acceptable only when reported honestly.",
        ),
        _case(
            "vector_retrieval_ready",
            expected={"embedding_status": "ready", "faiss_status": "ready"},
            actual={"embedding": retrieval.get("embedding_coverage"), "faiss": retrieval.get("faiss")},
            passed=not _vector_readiness_failures(retrieval),
            failures=_vector_readiness_failures(retrieval),
            reason="Product-ready retrieval requires complete embedding and FAISS coverage; partial vectors may be used only as a disclosed degraded mode.",
        ),
        *_retrieval_query_gate_cases(retrieval),
    ]
    independent_failures = [
        failure
        for case in cases
        for failure in case.get("blocking_failures", [])
        if str(failure or "")
    ]
    expected_quality_ready = not independent_failures
    quality_ready = quality.get("product_ready")
    repo_level_without_quality = mode == "post_apply" and quality_ready is None
    cases.append(
        _case(
            "quality_product_ready_matches_independent_gates",
            expected={"product_ready": expected_quality_ready},
            actual={"product_ready": quality_ready, "blocking_issues": quality.get("blocking_issues")},
            passed=repo_level_without_quality or quality_ready is expected_quality_ready,
            failures=[]
            if repo_level_without_quality or quality_ready is expected_quality_ready
            else ["quality_eval_understated_product_ready" if expected_quality_ready else "quality_eval_overstated_product_ready"],
            reason="Quality eval must agree with independent gates when a current job quality artifact is available; repo-level post-apply eval may run without a current job artifact.",
        ),
    )
    return cases


def _case(
    case_id: str,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    passed: bool,
    failures: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "expected_output_shape": expected,
        "actual_output_summary": actual,
        "passed": passed,
        "semantic_reason": reason,
        "blocking_failures": failures,
    }

def _compact_quality(payload: dict[str, Any]) -> dict[str, Any]:
    issues = payload.get("blocking_issues")
    if not isinstance(issues, list):
        issues = []
    compact_issues: list[Any] = []
    for issue in issues:
        if isinstance(issue, dict):
            compact_issues.append(
                {
                    "code": issue.get("code") or issue.get("reason") or "unknown",
                    "message": issue.get("message") or issue.get("stage") or "",
                }
            )
        else:
            compact_issues.append(issue)
    return {
        "ok": payload.get("ok"),
        "product_ready": payload.get("product_ready"),
        "blocking_issues": compact_issues,
    }


def _retrieval_projection_failures(retrieval: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not retrieval.get("repo_doc_count"):
        failures.append("retrieval_projection_missing")
    if retrieval.get("full_trace_dominated"):
        failures.append("retrieval_full_trace_dominated")
    return failures

def _retrieval_query_gate_cases(retrieval: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for gate in retrieval.get("query_gates", []):
        if not isinstance(gate, dict):
            continue
        cases.append(
            _case(
                str(gate.get("case_id") or "retrieval_query_gate"),
                expected={
                    "query": gate.get("query"),
                    "top_docs": "curated_or_central_support",
                    "forbidden": ["CodeNode", "CodeHunk", "session_codenode", "session_codehunk", "code"],
                },
                actual={
                    "hits": gate.get("hits"),
                    "forbidden_hits": gate.get("forbidden_hits"),
                    "expected_support_present": gate.get("expected_support_present"),
                },
                passed=bool(gate.get("passed")),
                failures=list(gate.get("blocking_failures") or []),
                reason=str(gate.get("semantic_reason") or "Production query gate must return curated or central support, not raw trace nodes."),
            )
        )
    return cases


def _quality_issue_codes(quality: dict[str, Any]) -> list[str]:
    issues = quality.get("blocking_issues")
    if not isinstance(issues, list):
        return []
    codes: list[str] = []
    for issue in issues:
        if isinstance(issue, dict):
            code = str(issue.get("code") or issue.get("reason") or "").strip()
        else:
            code = str(issue or "").strip()
        if code:
            codes.append(code)
    return codes


def _vector_readiness_failures(retrieval: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    embedding = retrieval.get("embedding_coverage") if isinstance(retrieval.get("embedding_coverage"), dict) else {}
    faiss = retrieval.get("faiss") if isinstance(retrieval.get("faiss"), dict) else {}
    embedding_status = str(embedding.get("status") or "missing")
    faiss_status = str(faiss.get("status") or "missing")
    if embedding_status != "ready":
        failures.append("embedding_coverage_missing" if embedding_status == "missing" else "embedding_coverage_partial")
    if faiss_status != "ready":
        failures.append("faiss_coverage_missing" if faiss_status == "missing" else "faiss_coverage_partial")
    return failures
