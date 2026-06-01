from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.application.services.memory_graph.service import GraphRagService
from agent_memory_orchestrator.infrastructure.kuzu import GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.domain.retrieval.models import RetrievalDocument
from agent_memory_orchestrator.infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from agent_memory_orchestrator.runtime.mcp.tools import MCP_MEMORY_TOOL_CONTRACTS, MemoryMcpToolService
from agent_memory_orchestrator.llm.qwen import DeterministicPlanner


def make_settings(tmp_path: Path) -> Settings:
    (tmp_path / ".data").mkdir(parents=True, exist_ok=True)
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "agent_memory.db",
        retrieval_db_path=tmp_path / ".data" / "retrieval.sqlite",
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


class _FakeIndexedGraph:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def retrieve_indexed_graph(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return self.payload

    def close(self) -> None:
        pass


def test_mcp_memory_tool_contracts_are_explicit(tmp_path) -> None:
    svc = MemoryMcpToolService(make_settings(tmp_path))
    try:
        contracts = svc.tool_contracts()
        assert contracts["ok"] is True
        for name in [
            "memory_write",
            "memory_search",
            "memory_context_pack",
            "memory_timeline",
            "memory_export",
            "memory_import",
            "amo_graph_search",
            "amo_current_context",
            "amo_decision_history",
            "amo_work_history",
            "amo_raw_evidence",
            "amo_merge_status",
        ]:
            assert name in contracts["tools"]
            assert MCP_MEMORY_TOOL_CONTRACTS[name]["required"] == contracts["tools"][name]["required"]
    finally:
        svc.close()


def test_mcp_graph_tools_are_explicit_and_do_not_use_hybrid_context_pack_injection(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="decision:mcp-graph-tools",
            kind="Decision",
            label="Explicit MCP GraphRAG tools",
            summary="Use Kuzu GraphRAG through explicit MCP tools, not hybrid context-pack injection.",
            status="committed",
            scope="central",
            source_app="codex",
        )
    )
    graph = GraphRagService(settings, store=store, planner=DeterministicPlanner())
    svc = MemoryMcpToolService(settings, graph=graph)
    try:
        graph.capture_hook(
            {
                "session_id": "graph-mcp-s1",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "final decision: use Kuzu GraphRAG through explicit MCP tools",
            },
            default_agent="codex",
        )

        result = svc.amo_graph_search(query="Kuzu GraphRAG explicit MCP tools", limit=3)
        status = svc.amo_merge_status(session_id="graph-mcp-s1")
    finally:
        svc.close()

    assert result["ok"] is False
    assert result["error"] == "active_repo_projection_missing"
    assert status["ok"] is True
    assert status["counts"]["draft"] >= 1


def test_mcp_graph_tool_without_injected_graph_requires_daemon(tmp_path) -> None:
    settings = make_settings(tmp_path)
    settings = Settings(**{**settings.__dict__, "mcp_port": 9})
    svc = MemoryMcpToolService(settings)
    try:
        result = svc.amo_graph_search(query="codex hooks", repo_id="repo:amo", limit=3)
    finally:
        svc.close()

    assert result["ok"] is False
    assert result["requires_daemon"] is True
    assert result["tool"] == "amo_graph_search"


def test_mcp_graph_search_uses_active_repo_projection_when_repo_id_is_provided(tmp_path) -> None:
    payload = {
        "ok": True,
        "retrieval": {
            "query": "why did graph service change?",
            "vector_status": "faiss:completed",
            "hits": [
                {
                    "score": 1.42,
                    "reasons": ["term_overlap:graph,service"],
                    "document": {
                        "doc_id": "doc:file-impact",
                        "doc_type": "file_impact",
                        "node_kind": "FileImpactSummary",
                        "repo_id": "repo:amo",
                        "packet_id": "WP0001",
                        "commit_sha": "abc1234",
                        "title": "Impact summary for src/agent_memory_orchestrator/graph/service.py",
                        "body": "FileImpactSummary: graph/service.py changed to render active repository retrieval context.",
                        "metadata": {
                            "path": "src/agent_memory_orchestrator/graph/service.py",
                            "commit": {"message": "feat(retrieval): render repository context"},
                            "problem_refs": [{"excerpt": "The answer only showed prompt text."}],
                            "rationale_refs": [{"excerpt": "Render file impact and version context for MCP."}],
                        },
                    },
                }
            ],
        },
        "answer": {
            "text": "Answer from repository memory:\nUse this as retrieval context for synthesis.",
            "context": {
                "version_timeline": {
                    "entries": [
                        {
                            "commit_sha": "abc1234",
                            "message": "feat(retrieval): render repository context",
                            "why": "Render file impact and version context for MCP.",
                        }
                    ]
                }
            },
        },
    }
    graph = _FakeIndexedGraph(payload)
    svc = MemoryMcpToolService(make_settings(tmp_path), graph=graph)  # type: ignore[arg-type]
    try:
        result = svc.amo_graph_search(
            query="why did graph service change?",
            repo_id="repo:amo",
            limit=5,
            require_vector=True,
        )
    finally:
        svc.close()

    assert graph.calls[0]["repo_id"] == "repo:amo"
    assert graph.calls[0]["require_vector"] is True
    assert result["ok"] is True
    assert result["retrieval_mode"] == "active_repository_memory"
    assert "retrieval context" in result["context_for_synthesis"]
    assert result["retrieval_status"]["vector"] == "faiss:completed"
    assert result["hits"][0]["kind"] == "file_impact"
    assert result["hits"][0]["commit"]["sha"] == "abc1234"
    assert result["hits"][0]["evidence"][0]["role"] == "user_goal"
    assert result["version_history"][0]["commit"] == "abc1234"


def test_mcp_graph_search_resolves_repo_name_to_active_projection(tmp_path) -> None:
    settings = make_settings(tmp_path)
    repo_id = "repo:remote:311ebb9cda1fb40f"
    projection_id = "rproj:amo"
    conn = sqlite3.connect(settings.retrieval_db_path)
    conn.row_factory = sqlite3.Row
    index_store = RetrievalIndexStore(conn)
    try:
        index_store.upsert_projection(
            projection_id=projection_id,
            repo_id=repo_id,
            projection_version="curated-retrieval-projection-v1",
            source_artifact_hash="source",
            doc_content_hash="docs",
            status="validated",
        )
        index_store.activate_projection(repo_id=repo_id, projection_id=projection_id)
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="doc:packet",
                    doc_type="packet",
                    graph_node_id="job:WP0001",
                    node_kind="Packet",
                    repo_id=repo_id,
                    projection_id=projection_id,
                    packet_id="WP0001",
                    commit_sha="abc1234",
                    title="WP0001 Add AMO demo",
                    body="Packet for AMO demo.",
                    metadata={"repo_path": r"C:\Users\sumit\Downloads\Dora\agent-memory-orchestrator"},
                )
            ]
        )
    finally:
        conn.close()

    payload = {
        "ok": True,
        "retrieval": {
            "vector_status": "faiss:completed",
            "hits": [
                {
                    "score": 1.0,
                    "document": {
                        "doc_id": "doc:packet",
                        "doc_type": "packet",
                        "node_kind": "Packet",
                        "repo_id": repo_id,
                        "title": "WP0001 Add AMO demo",
                        "body": "Packet for AMO demo.",
                        "metadata": {"problem_refs": [{"excerpt": "Build AMO demo retrieval."}]},
                    },
                }
            ],
        },
        "answer": {"text": "Answer from repository memory:"},
    }
    graph = _FakeIndexedGraph(payload)
    svc = MemoryMcpToolService(settings, graph=graph)  # type: ignore[arg-type]
    try:
        result = svc.amo_graph_search(query="why was demo created", repo_id="agent-memory-orchestrator", limit=3)
        default_result = svc.amo_graph_search(query="why was demo created", limit=3)
    finally:
        svc.close()

    assert graph.calls[0]["repo_id"] == repo_id
    assert graph.calls[1]["repo_id"] == repo_id
    assert result["ok"] is True
    assert default_result["ok"] is True
    assert result["repo"]["id"] == repo_id
    assert default_result["repo"]["id"] == repo_id


def test_mcp_memory_write_search_context_and_timeline(tmp_path) -> None:
    svc = MemoryMcpToolService(make_settings(tmp_path))
    try:
        written = svc.memory_write(
            session_id="mcp-s1",
            agent="codex",
            event_type="response",
            content=(
                "Decision: scraper/retry.py uses exponential backoff with jitter because fixed delay "
                "caused rate limits."
            ),
            metadata_json=json.dumps({"turn_id": "turn-1"}),
        )
        assert written["ok"] is True
        assert written["event_id"]
        assert written["memory_count"] >= 1
        assert written["memory_ids"]

        search = svc.memory_search(query="why did retry jitter change", session_id="mcp-s1", limit=5)
        assert search["ok"] is True
        assert search["count"] >= 1
        assert search["results"][0]["memory_id"] in written["memory_ids"]
        assert search["results"][0]["retrieval_run_id"]

        pack = svc.memory_context_pack(query="why did retry jitter change", session_id="mcp-s1", budget=900, limit=5)
        assert pack["ok"] is True
        assert "AMO local memory context" in pack["text"]
        assert pack["items"]
        assert pack["items"][0]["memory_id"] in written["memory_ids"]

        timeline = svc.memory_timeline(session_id="mcp-s1", limit=10)
        assert timeline["ok"] is True
        assert timeline["count"] == 1
        assert timeline["events"][0]["metadata"]["turn_id"] == "turn-1"
    finally:
        svc.close()


def test_mcp_memory_export_import_round_trip(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryMcpToolService(settings)
    try:
        svc.memory_write(
            session_id="export-s1",
            agent="claude",
            event_type="response",
            content="Implemented .codex/config.toml Codex hooks with UserPromptSubmit for memory retrieval.",
        )
        out_path = tmp_path / "exports" / "snapshot.jsonl"
        exported = svc.memory_export(out_path=str(out_path), session_id="export-s1")
        assert exported["ok"] is True
        assert exported["rows"] > 0
        assert out_path.exists()
    finally:
        svc.close()

    imported_root = tmp_path / "imported"
    imported_root.mkdir(parents=True, exist_ok=True)
    imported_settings = make_settings(imported_root)
    imported = MemoryMcpToolService(imported_settings)
    try:
        result = imported.memory_import(in_path=str(out_path))
        assert result["ok"] is True
        assert result["rows"] == exported["rows"]
        hits = imported.memory_search(query="Codex hooks UserPromptSubmit", session_id="export-s1", limit=5)
        assert hits["count"] >= 1
    finally:
        imported.close()


def test_mcp_memory_tool_validation(tmp_path) -> None:
    svc = MemoryMcpToolService(make_settings(tmp_path))
    try:
        with pytest.raises(ValueError, match="agent must be one of"):
            svc.memory_write(
                session_id="s1",
                agent="unknown",
                event_type="response",
                content="Decision: invalid agent should fail.",
            )
        with pytest.raises(ValueError, match="metadata_json must be a JSON object"):
            svc.memory_write(
                session_id="s1",
                agent="codex",
                event_type="response",
                content="Decision: metadata must be an object.",
                metadata_json="[]",
            )
        limited = svc.memory_search(query="nothing exists", limit=1000)
        assert limited["ok"] is True
        assert limited["count"] == 0
    finally:
        svc.close()
