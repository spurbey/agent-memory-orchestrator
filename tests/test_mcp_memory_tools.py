from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph_service import GraphRagService
from agent_memory_orchestrator.graph_store import GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.mcp_memory_tools import MCP_MEMORY_TOOL_CONTRACTS, MemoryMcpToolService
from agent_memory_orchestrator.qwen_client import DeterministicPlanner


def make_settings(tmp_path: Path) -> Settings:
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


def test_mcp_graph_tools_are_explicit_and_do_not_use_legacy_context_pack(tmp_path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="decision:mcp-graph-tools",
            kind="Decision",
            label="Explicit MCP GraphRAG tools",
            summary="Use Kuzu GraphRAG through explicit MCP tools, not legacy context-pack injection.",
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

    assert result["ok"] is True
    assert result["count"] >= 1
    assert "AMO GraphRAG context" in result["context"]
    assert status["ok"] is True
    assert status["counts"]["draft"] >= 1


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
