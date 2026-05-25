from __future__ import annotations

from agent_memory_orchestrator.reasoning_graph.jobs.runner import _quality_issues


def test_quality_issues_block_dry_run_central_merge_and_partial_vectors() -> None:
    issues = _quality_issues(
        central_result={"status": "planned", "mode": "dry_run", "plan_id": "v2plan:test"},
        retrieval_result={"doc_count": 10},
        embedding_result={"total_docs": 10, "embedded": 3, "already_embedded": 2, "limit_hit": True},
        faiss_result={"status": "completed", "item_count": 5},
    )

    assert {issue["code"] for issue in issues} == {
        "active_projection_missing",
        "central_merge_not_applied",
        "embedding_coverage_partial",
        "faiss_coverage_partial",
    }
