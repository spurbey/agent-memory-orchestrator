from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph_service import GraphRagService
from agent_memory_orchestrator.graph_store import GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.qwen_client import DeterministicPlanner
from agent_memory_orchestrator.versioning import GitSnapshot


class _StaticGitBackend:
    def __init__(self, snapshot: GitSnapshot | None = None) -> None:
        self._snapshot = snapshot or GitSnapshot(available=False)

    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return self._snapshot


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "legacy.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="disabled",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=tmp_path / "graph" / "amo.kuzu",
        evidence_dir=tmp_path / "evidence",
    )


def test_hook_capture_writes_raw_evidence_and_no_prompt_injection(tmp_path: Path) -> None:
    svc = GraphRagService(
        make_settings(tmp_path),
        store=InMemoryGraphStore(),
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        result = svc.capture_hook(
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "what did we decide about codex hooks",
            },
            default_agent="codex",
        )
    finally:
        svc.close()

    assert result["ok"] is True
    assert result["capture_only"] is True
    assert result["additional_context"] == ""
    assert Path(result["evidence"]["path"]).exists()


def test_session_start_returns_only_startup_status_context(tmp_path: Path) -> None:
    svc = GraphRagService(
        make_settings(tmp_path),
        store=InMemoryGraphStore(),
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        result = svc.capture_hook({"session_id": "s1", "hook_event_name": "SessionStart"}, default_agent="codex")
    finally:
        svc.close()

    assert "AMO GraphRAG is active" in result["additional_context"]
    assert "amo_graph_search" in result["additional_context"]


def test_explicit_graph_search_uses_graph_nodes_not_legacy_memory_rows(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="decision:codex-hooks",
            kind="Decision",
            label="Codex hooks retrieval policy",
            summary="Use Codex hooks for capture and MCP graph search for explicit retrieval.",
            status="committed",
            scope="central",
            source_app="codex",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        result = svc.graph_search(query="codex hooks MCP graph search", limit=3)
    finally:
        svc.close()

    assert result["ok"] is True
    assert result["count"] >= 1
    assert "AMO GraphRAG context" in result["context"]
    assert any(node["kind"] == "Decision" for node in result["nodes"])


def test_graph_search_excludes_raw_evidence_without_explicit_raw_request(tmp_path: Path) -> None:
    svc = GraphRagService(
        make_settings(tmp_path),
        store=InMemoryGraphStore(),
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        svc.capture_hook(
            {
                "session_id": "s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "what did we decide about codex hooks",
            },
            default_agent="codex",
        )
        result = svc.graph_search(query="what did we decide about codex hooks", limit=3)
    finally:
        svc.close()

    assert result["ok"] is True
    assert result["count"] == 0
    assert result["nodes"] == []
    assert "No answer-grade graph memory" in result["context"]


def test_commit_event_auto_links_session_to_git_commit(tmp_path: Path) -> None:
    git = GitSnapshot(
        available=True,
        repo_root=str(tmp_path / "repo"),
        branch="main",
        head="abcdef1234567890",
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=InMemoryGraphStore(),
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(git),
    )
    try:
        result = svc.capture_hook(
            {
                "session_id": "s1",
                "hook_event_name": "PostToolUse",
                "content": "git commit -m graph-rag\n[main abcdef1] graph-rag",
            },
            default_agent="codex",
        )
        status = svc.merge_status(session_id="s1")
    finally:
        svc.close()

    assert result["merge"]["merged"] is True
    assert result["merge"]["commit"] == "abcdef1234567890"
    assert status["counts"]["committed"] == 1
