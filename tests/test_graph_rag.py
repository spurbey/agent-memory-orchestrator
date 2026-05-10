from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph.service import GraphRagService
from agent_memory_orchestrator.graph.store import GraphEdge, GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.llm.qwen import DeterministicPlanner
from agent_memory_orchestrator.versioning import GitSnapshot


class _StaticGitBackend:
    def __init__(self, snapshot: GitSnapshot | None = None) -> None:
        self._snapshot = snapshot or GitSnapshot(available=False)

    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return self._snapshot


class _PollutedSeedStore(InMemoryGraphStore):
    def search_nodes(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict]:
        raw = self.nodes.get("evidence:raw_write")
        return [{**raw.as_dict(), "graph_score": 1.0}] if raw else []


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


def test_raw_word_in_topic_does_not_enable_raw_evidence_retrieval(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="context:clean-window-smoke:latest",
            kind="ContextSnapshot",
            label="latest context for clean-window-smoke",
            summary="Clean raw artifacts before graph extraction",
            status="draft",
            scope="session",
            session_id="clean-window-smoke",
            evidence_id="raw_write",
            metadata={
                "goal": "clean raw artifacts before graph extraction",
                "changed_files": ["src/agent_memory_orchestrator/evidence_window.py"],
                "trigger": {"trigger_type": "write"},
            },
        )
    )
    store.upsert_node(
        GraphNode(
            id="evidence:raw_write",
            kind="RawEvidenceRef",
            label="raw_write",
            summary="post_tool_use raw evidence from codex",
            status="draft",
            scope="session",
            session_id="clean-window-smoke",
            evidence_id="raw_write",
        )
    )
    store.upsert_node(
        GraphNode(
            id="decision:weak-graph-match",
            kind="Decision",
            label="GraphRAG versioning cleanup",
            summary="GraphRAG versioning cleanup remains planned.",
            status="draft",
            scope="session",
            session_id="other-session",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        result = svc.graph_search(query="clean raw artifacts before graph extraction", limit=5)
    finally:
        svc.close()

    assert result["raw_included"] is False
    assert result["plan"]["include_raw"] is False
    assert [node["id"] for node in result["nodes"]] == ["context:clean-window-smoke:latest"]


def test_graph_search_recovers_context_when_seed_batch_is_polluted_by_raw_nodes(tmp_path: Path) -> None:
    store = _PollutedSeedStore()
    store.upsert_node(
        GraphNode(
            id="context:clean-window-smoke:latest",
            kind="ContextSnapshot",
            label="latest context for clean-window-smoke",
            summary="Clean raw artifacts before graph extraction",
            status="draft",
            scope="session",
            session_id="clean-window-smoke",
            evidence_id="raw_write",
            metadata={
                "goal": "clean raw artifacts before graph extraction",
                "changed_files": ["src/agent_memory_orchestrator/evidence_window.py"],
                "trigger": {"trigger_type": "write"},
            },
        )
    )
    store.upsert_node(
        GraphNode(
            id="evidence:raw_write",
            kind="RawEvidenceRef",
            label="raw_write",
            summary="post_tool_use raw evidence from codex",
            status="draft",
            scope="session",
            session_id="clean-window-smoke",
            evidence_id="raw_write",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        result = svc.graph_search(query="clean raw artifacts before graph extraction", limit=5)
    finally:
        svc.close()

    assert result["raw_included"] is False
    assert [node["id"] for node in result["nodes"]] == ["context:clean-window-smoke:latest"]


def test_explicit_raw_evidence_query_can_return_raw_nodes(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="evidence:raw_write",
            kind="RawEvidenceRef",
            label="raw_write",
            summary="post_tool_use raw evidence from codex",
            status="draft",
            scope="session",
            session_id="clean-window-smoke",
            evidence_id="raw_write",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        result = svc.graph_search(query="show raw evidence for clean-window-smoke", limit=5)
    finally:
        svc.close()

    assert result["raw_included"] is True
    assert any(node["kind"] == "RawEvidenceRef" for node in result["nodes"])


def test_stop_events_do_not_become_current_context_snapshots(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        svc.capture_hook(
            {
                "session_id": "s1",
                "hook_event_name": "Stop",
                "last_assistant_message": "weather answer should stay raw evidence only",
            },
            default_agent="codex",
        )
        context = svc.current_context(session_id="s1")
        search = svc.graph_search(query="weather answer", limit=3)
    finally:
        svc.close()

    assert context["count"] == 0
    assert all(not node["id"].startswith("event:raw_") for node in context["nodes"])
    assert search["count"] == 0


def test_current_context_filters_noisy_legacy_context_snapshots(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="context:s1:latest",
            kind="ContextSnapshot",
            label="latest context for s1",
            summary='"continue": true, "manualSmoke": false, "captureOnly": true, raw_abc',
            status="draft",
            scope="session",
            session_id="s1",
            metadata={"changed_files": ["hook.py"], "next_step": "Review graph context."},
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        context = svc.current_context(session_id="s1")
        search = svc.graph_search(query="captureOnly context", limit=3)
    finally:
        svc.close()

    assert context["count"] == 0
    assert search["count"] == 0


def test_graph_search_filters_noisy_legacy_work_changes(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="work:s1:noisy",
            kind="WorkChange",
            label="raw hook payload",
            summary='"continue": true, "manualSmoke": false, "captureOnly": true, raw_abc after_preview',
            status="draft",
            scope="session",
            session_id="s1",
            metadata={"trigger": {"trigger_type": "write"}},
        )
    )
    store.upsert_node(
        GraphNode(
            id="work:s1:clean",
            kind="WorkChange",
            label="GraphRAG cleanup",
            summary="Updated GraphRAG retrieval to filter noisy draft work changes.",
            status="draft",
            scope="session",
            session_id="s1",
            metadata={"trigger": {"trigger_type": "write"}},
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        search = svc.graph_search(query="GraphRAG noisy work changes", limit=5)
        cleanup = svc.cleanup_noisy_drafts(apply=True)
    finally:
        svc.close()

    assert [node["id"] for node in search["nodes"]] == ["work:s1:clean"]
    assert cleanup["noisy_count"] == 1
    assert store.nodes["work:s1:noisy"].status == "abandoned"


def test_graph_search_does_not_return_support_only_file_nodes_as_answers(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="file:clean-window-smoke.py",
            kind="File",
            label="clean-window-smoke.py",
            summary="File touched by AMO work ledger: clean-window-smoke.py",
            status="active",
            scope="central",
        )
    )
    store.upsert_node(
        GraphNode(
            id="bug:s1:noisy-assertion",
            kind="Bug",
            label='raise AssertionError("manual hook smoke mode must not block")',
            summary='raise AssertionError("manual hook smoke mode must not block")',
            status="draft",
            scope="session",
            session_id="s1",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        search = svc.graph_search(query="clean window smoke", limit=5)
    finally:
        svc.close()

    assert search["count"] == 0
    assert search["nodes"] == []


def test_graph_search_does_not_return_graph_delta_as_answer(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="delta:s1:raw_write",
            kind="GraphDelta",
            label="Clean raw artifacts before graph extraction",
            summary="Clean raw artifacts before graph extraction",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw_write",
        )
    )
    store.upsert_node(
        GraphNode(
            id="context:s1:latest",
            kind="ContextSnapshot",
            label="latest context for s1",
            summary="Clean raw artifacts before graph extraction",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw_write",
            metadata={"changed_files": ["src/agent_memory_orchestrator/evidence_window.py"]},
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:delta-context",
            source_id="delta:s1:raw_write",
            target_id="context:s1:latest",
            kind="CREATED",
            evidence_id="raw_write",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        search = svc.graph_search(query="clean raw artifacts graph extraction", limit=5)
    finally:
        svc.close()

    assert [node["id"] for node in search["nodes"]] == ["context:s1:latest"]


def test_write_context_with_changed_files_is_answer_grade(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="context:s1:latest",
            kind="ContextSnapshot",
            label="latest context for s1",
            summary="Clean raw artifacts before graph extraction",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw_write",
            metadata={
                "goal": "['clean raw artifacts before graph extraction']",
                "latest_decision": "['Clean raw artifacts before graph extraction']",
                "next_step": "['Apply the code edit to the specified file']",
                "changed_files": ["src/agent_memory_orchestrator/evidence_window.py"],
                "trigger": {
                    "should_process": True,
                    "trigger_type": "write",
                    "reason": "write/edit tool detected",
                    "is_write": True,
                },
            },
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        context = svc.current_context(session_id="s1")
        search = svc.graph_search(query="clean raw artifacts graph extraction", limit=5)
    finally:
        svc.close()

    assert context["count"] == 1
    assert context["nodes"][0]["metadata"]["goal"] == "clean raw artifacts before graph extraction"
    assert "['" not in context["context"]
    assert search["count"] == 1
    assert search["nodes"][0]["id"] == "context:s1:latest"


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
