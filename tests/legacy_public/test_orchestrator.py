from __future__ import annotations

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.orchestration import OrchestratorService


def make_settings(tmp_path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "agent_memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="sqlite",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        context_budget=2500,
        reranker_backend="lexical",
        rerank_top_k=50,
        rerank_max_chars=1800,
    )


def test_orchestrator_full_review_cycle(tmp_path) -> None:
    orch = OrchestratorService(make_settings(tmp_path))
    started = orch.start("s1", "Session 1")
    assert started["session"]["status"] == "draft"

    after_claude = orch.submit(
        session_id="s1",
        agent="claude",
        summary="Initial architecture draft.",
        confidence=0.9,
    )
    assert after_claude["session"]["status"] == "review"

    after_codex_blocker = orch.submit(
        session_id="s1",
        agent="codex",
        summary="Found a missing rollback path.",
        confidence=0.6,
        blocking_issues=["Missing rollback path"],
    )
    assert after_codex_blocker["session"]["status"] == "revise"

    after_revise = orch.submit(
        session_id="s1",
        agent="claude",
        summary="Added rollback handling and retry policy.",
        confidence=0.9,
    )
    assert after_revise["session"]["status"] == "review"

    ready = orch.submit(
        session_id="s1",
        agent="codex",
        summary="No blockers remain.",
        confidence=0.92,
    )
    assert ready["session"]["status"] == "ready_for_user"

    approved = orch.user_decision("s1", "approved", notes="Ship it.")
    assert approved["session"]["status"] == "approved"
    orch.close()


def test_orchestrator_rejects_invalid_decision_timing(tmp_path) -> None:
    orch = OrchestratorService(make_settings(tmp_path))
    orch.start("s2", "Session 2")
    with pytest.raises(ValueError, match="ready_for_user"):
        orch.user_decision("s2", "approved")
    orch.close()

