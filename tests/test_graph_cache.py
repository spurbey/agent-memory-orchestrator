from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph_cache import GraphSearchCache
from agent_memory_orchestrator.graph_service import GraphRagService
from agent_memory_orchestrator.graph_store import GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.qwen_client import DeterministicPlanner
from agent_memory_orchestrator.versioning import GitSnapshot


class _StaticGitBackend:
    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return GitSnapshot(available=False)


class _NoSearchStore(InMemoryGraphStore):
    def search_nodes(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict]:
        return []


def test_graph_search_cache_rebuilds_and_searches_nodes(tmp_path: Path) -> None:
    cache = GraphSearchCache(tmp_path / "graph_nodes_bm25.json")
    result = cache.rebuild(
        [
            GraphNode(
                id="decision:clean-window",
                kind="Decision",
                label="Clean raw artifacts before graph extraction",
                summary="Clean raw artifacts before graph extraction and Qwen summarization.",
                evidence_id="raw_write",
            ).as_dict()
        ]
    )
    hits = cache.search("clean graph extraction", limit=3, kinds=["Decision"])
    status = cache.status()

    assert result["doc_count"] == 1
    assert status["exists"] is True
    assert hits[0]["id"] == "decision:clean-window"
    assert hits[0]["cache_hit"] is True


def test_graph_search_uses_rebuilt_cache_when_graph_text_search_misses(tmp_path: Path) -> None:
    store = _NoSearchStore()
    store.upsert_node(
        GraphNode(
            id="context:clean-window:latest",
            kind="ContextSnapshot",
            label="latest context for clean-window",
            summary="Clean artifacts before graph extraction",
            status="draft",
            scope="session",
            session_id="clean-window",
            evidence_id="raw_write",
            metadata={
                "goal": "clean artifacts before graph extraction",
                "changed_files": ["src/agent_memory_orchestrator/evidence_window.py"],
                "trigger": {"trigger_type": "write"},
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
        rebuild = svc.rebuild_graph_cache()
        result = svc.graph_search(query="clean artifacts graph extraction", limit=5)
    finally:
        svc.close()

    assert rebuild["doc_count"] == 1
    assert [node["id"] for node in result["nodes"]] == ["context:clean-window:latest"]
    assert result["nodes"][0]["cache_hit"] is True


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
