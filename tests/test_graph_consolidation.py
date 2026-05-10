from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph.service import GraphRagService
from agent_memory_orchestrator.graph.store import GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.llm.qwen import DeterministicPlanner
from agent_memory_orchestrator.versioning import GitSnapshot


class _StaticGitBackend:
    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return GitSnapshot(available=False)


def test_graph_consolidation_classifies_and_writes_edges(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="decision:old",
            kind="Decision",
            label="Use daemon-owned Kuzu graph runtime",
            summary="Use daemon-owned Kuzu graph runtime for graph access.",
            session_id="s1",
        )
    )
    store.upsert_node(
        GraphNode(
            id="decision:new",
            kind="Decision",
            label="Use daemon-owned Kuzu graph runtime",
            summary="Use daemon-owned Kuzu graph runtime for graph access.",
            session_id="s2",
        )
    )
    store.upsert_node(
        GraphNode(
            id="work:refine",
            kind="WorkChange",
            label="Improve daemon-owned Kuzu graph runtime",
            summary="Improve daemon-owned Kuzu graph runtime by adding graph consolidation edges.",
            session_id="s2",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        preview = svc.consolidate_graph(limit=20, apply=False)
        applied = svc.consolidate_graph(limit=20, apply=True)
        edges = store.list_edges(limit=20)
        topics = store.list_nodes(kinds=["Topic"], limit=10)
    finally:
        svc.close()

    assert preview["candidate_count"] >= 1
    assert applied["edges_written"] >= 1
    edge_kinds = {edge["kind"] for edge in edges}
    assert "DUPLICATE_OF" in edge_kinds
    assert {"REFINES", "MEMBER_OF"} & edge_kinds
    assert topics


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
