from __future__ import annotations

import sqlite3
from pathlib import Path

from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph.service import GraphRagService
from agent_memory_orchestrator.graph.store import GraphEdge
from agent_memory_orchestrator.graph.store import GraphNode
from agent_memory_orchestrator.graph.store import InMemoryGraphStore
from agent_memory_orchestrator.llm.qwen import DeterministicPlanner
from agent_memory_orchestrator.reasoning_graph import GraphEmbeddingStore
from agent_memory_orchestrator.reasoning_graph import RetrievalIndexStore
from agent_memory_orchestrator.reasoning_graph import build_retrieval_documents_from_graph
from agent_memory_orchestrator.reasoning_graph import classify_query
from agent_memory_orchestrator.reasoning_graph import embed_missing_retrieval_documents
from agent_memory_orchestrator.reasoning_graph import retrieve_session_graph


class _KeywordEmbedder:
    def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if "bm25" in lowered or "vector" in lowered or "retrieval" in lowered:
            return [1.0, 0.0, 0.0]
        if "dashboard" in lowered:
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "retrieval.sqlite",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=16,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="faiss",
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


def _sqlite_store(tmp_path: Path) -> tuple[sqlite3.Connection, RetrievalIndexStore, GraphEmbeddingStore]:
    db_path = tmp_path / "retrieval.sqlite"
    conn = connect(db_path)
    return conn, RetrievalIndexStore(conn), GraphEmbeddingStore(conn, db_path=db_path)


def _graph() -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:WP0001:decision:retrieval",
            kind="ReasoningNode",
            label="Retrieval architecture decision",
            summary="Use exact lookup, BM25, vector retrieval, RRF fusion, then graph expansion.",
            status="accepted",
            session_id="s1",
            commit_id="abc1234",
            metadata={
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "node_type": "Decision",
                "statement": "Use exact lookup, BM25, vector retrieval, RRF fusion, then graph expansion.",
                "paths": ["src/agent_memory_orchestrator/reasoning_graph/retrieval.py"],
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="code:retrieval:retrieve_session_graph",
            kind="CodeNode",
            label="retrieve_session_graph",
            summary="Implements query classification, candidate fusion, and graph neighbor expansion.",
            status="accepted",
            session_id="s1",
            commit_id="abc1234",
            metadata={
                "file_path": "src/agent_memory_orchestrator/reasoning_graph/retrieval.py",
                "symbol": "retrieve_session_graph",
                "commit_sha": "abc1234",
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="symbol:retrieval:retrieve_session_graph",
            kind="Symbol",
            label="src/agent_memory_orchestrator/reasoning_graph/retrieval.py::retrieve_session_graph",
            summary="Symbol version flow for retrieve_session_graph.",
            status="accepted",
            session_id="s1",
            commit_id="abc1234",
            metadata={
                "file_path": "src/agent_memory_orchestrator/reasoning_graph/retrieval.py",
                "symbol": "retrieve_session_graph",
                "version_count": 1,
            },
        )
    )
    graph.upsert_edge(
        GraphEdge(
            id="edge:reason-code",
            source_id="reason:WP0001:decision:retrieval",
            target_id="code:retrieval:retrieve_session_graph",
            kind="IMPLEMENTS",
        )
    )
    return graph


def test_retrieval_documents_are_graph_attached_and_fts_indexed(tmp_path: Path) -> None:
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    docs = build_retrieval_documents_from_graph(_graph(), session_id="s1")

    written = index_store.upsert_documents(docs)
    hits = index_store.bm25_search("why bm25 vector graph expansion", limit=5)
    hit_docs = index_store.get_documents_by_ids(hit.doc_id for hit in hits)

    assert written == len(docs)
    assert {doc.doc_type for doc in docs} == {"reasoning", "code", "symbol"}
    assert hits
    assert hit_docs[hits[0].doc_id].graph_node_id.startswith("reason:")


def test_embedding_missing_retrieval_documents_is_resumable(tmp_path: Path) -> None:
    conn, index_store, embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(build_retrieval_documents_from_graph(_graph(), session_id="s1"))

    first = embed_missing_retrieval_documents(
        index_store=index_store,
        embedding_store=embedding_store,
        embedder=_KeywordEmbedder(),
        model="test-embedder",
        graph_scope="test-graph",
        session_id="s1",
        extraction_run_id="run1",
        limit=1,
    )
    second = embed_missing_retrieval_documents(
        index_store=index_store,
        embedding_store=embedding_store,
        embedder=_KeywordEmbedder(),
        model="test-embedder",
        graph_scope="test-graph",
        session_id="s1",
        extraction_run_id="run1",
    )
    third = embed_missing_retrieval_documents(
        index_store=index_store,
        embedding_store=embedding_store,
        embedder=_KeywordEmbedder(),
        model="test-embedder",
        graph_scope="test-graph",
        session_id="s1",
        extraction_run_id="run1",
    )
    count = conn.execute("SELECT count(*) FROM graph_embeddings").fetchone()[0]

    assert first.embedded == 1
    assert first.limit_hit is True
    assert second.already_embedded == 1
    assert second.embedded == 2
    assert third.embedded == 0
    assert third.already_embedded == 3
    assert count == 3


def test_retrieve_session_graph_fuses_candidates_and_expands_after_ranking(tmp_path: Path) -> None:
    graph = _graph()
    _conn, index_store, embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(build_retrieval_documents_from_graph(graph, session_id="s1"))
    embed_missing_retrieval_documents(
        index_store=index_store,
        embedding_store=embedding_store,
        embedder=_KeywordEmbedder(),
        model="test-embedder",
        graph_scope="test-graph",
        session_id="s1",
        extraction_run_id="run1",
    )

    result = retrieve_session_graph(
        query="why use BM25 vector retrieval before graph expansion",
        index_store=index_store,
        graph_store=graph,
        embedding_store=embedding_store,
        embedder=_KeywordEmbedder(),
        embedding_model="test-embedder",
        graph_scope="test-graph",
        session_id="s1",
        limit=3,
        expand_neighbors=5,
    )

    assert result.intent == "code_why"
    assert result.vector_status == "sqlite:completed"
    assert result.candidate_counts["bm25"] > 0
    assert result.candidate_counts["vector"] > 0
    assert result.hits[0].document.doc_type == "reasoning"
    assert result.hits[0].document.graph_node_id == "reason:WP0001:decision:retrieval"
    assert any(neighbor["id"] == "code:retrieval:retrieve_session_graph" for neighbor in result.hits[0].neighbors)


def test_version_flow_queries_boost_symbol_or_code_docs(tmp_path: Path) -> None:
    graph = _graph()
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(build_retrieval_documents_from_graph(graph, session_id="s1"))

    result = retrieve_session_graph(
        query="show version flow for retrieval.py::retrieve_session_graph",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=3,
        expand_neighbors=0,
    )

    assert classify_query("show version flow for retrieval.py::retrieve_session_graph") == "version_flow"
    assert result.hits
    assert result.hits[0].document.doc_type in {"symbol", "code"}


def test_graph_service_wires_retrieval_build_embed_and_answer(tmp_path: Path) -> None:
    svc = GraphRagService(
        _settings(tmp_path),
        store=_graph(),
        planner=DeterministicPlanner(),
    )
    try:
        build = svc.rebuild_retrieval_index(session_id="s1")
        embed = svc.embed_retrieval_index(session_id="s1", limit=0, rebuild_faiss=False)
        result = svc.retrieve_indexed_graph(
            query="why use BM25 vector retrieval before graph expansion",
            session_id="s1",
            limit=3,
            use_vector=True,
        )
    finally:
        svc.close()

    assert build["ok"] is True
    assert build["retrieval_document_count"] == 3
    assert embed["ok"] is True
    assert embed["embedding"]["embedded"] == 3
    assert result["ok"] is True
    assert result["retrieval"]["hits"]
    assert "AMO indexed graph answer" in result["answer"]["text"]
