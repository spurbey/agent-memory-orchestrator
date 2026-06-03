from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.application.pipeline.job_runner import _central_merge_quality_result
from agent_memory_orchestrator.application.pipeline.job_runner import _quality_issues


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


def test_quality_issues_require_applied_status_and_apply_mode() -> None:
    for central_result in (
        {"status": "applied", "mode": "dry_run", "plan_id": "v2plan:test"},
        {"status": "planned", "mode": "apply_exact_atoms", "plan_id": "v2plan:test"},
    ):
        issues = _quality_issues(
            central_result=central_result,
            retrieval_result={"doc_count": 1, "retrieval_source": "curated_graph_manifest", "active_projection_id": "rproj:test"},
            embedding_result={"total_docs": 1, "embedded": 1, "already_embedded": 0},
            faiss_result={"status": "completed", "item_count": 1},
        )

        assert "central_merge_not_applied" in {issue["code"] for issue in issues}


def test_quality_result_ignores_stale_merge_result_for_different_plan(tmp_path: Path) -> None:
    stage_dir = tmp_path / "central_version_merge"
    stage_dir.mkdir()
    (stage_dir / "merge_plan.json").write_text(
        json.dumps({"plan_id": "v2plan:new", "status": "planned", "mode": "dry_run"}),
        encoding="utf-8",
    )
    (stage_dir / "merge_result.json").write_text(
        json.dumps(
            {
                "plan_id": "v2plan:old",
                "status": "applied",
                "mode": "apply_exact_atoms",
                "graph_commit": {"graph_commit_id": "v2gcommit:old"},
            }
        ),
        encoding="utf-8",
    )

    result = _central_merge_quality_result(tmp_path)
    issues = _quality_issues(
        central_result=result,
        retrieval_result={"doc_count": 1, "retrieval_source": "curated_graph_manifest", "active_projection_id": "rproj:test"},
        embedding_result={"total_docs": 1, "embedded": 1, "already_embedded": 0},
        faiss_result={"status": "completed", "item_count": 1},
    )

    assert result["plan_id"] == "v2plan:new"
    assert result["status"] == "planned"
    assert result["stale_merge_result"]["plan_id"] == "v2plan:old"
    assert {"central_merge_not_applied", "central_merge_result_stale"} <= {issue["code"] for issue in issues}
