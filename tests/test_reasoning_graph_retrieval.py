from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.graph.service import GraphRagService
from agent_memory_orchestrator.graph.service import _active_central_versions_for_support
from agent_memory_orchestrator.graph.service import _answer_from_retrieval_result
from agent_memory_orchestrator.graph.service import _unique_nonempty
from agent_memory_orchestrator.graph.answer_trace import build_answer_trace
from agent_memory_orchestrator.graph.answer_trace import build_central_answer_trace
from agent_memory_orchestrator.graph.answer_trace import format_answer_trace
from agent_memory_orchestrator.graph.store import GraphEdge
from agent_memory_orchestrator.graph.store import GraphNode
from agent_memory_orchestrator.graph.store import InMemoryGraphStore
from agent_memory_orchestrator.runtime.cli import main as cli_module
from agent_memory_orchestrator.runtime.cli.commands import graph as graph_cli_module
from agent_memory_orchestrator.runtime.cli.main import _retrieve_index_only
from agent_memory_orchestrator.llm.qwen import DeterministicPlanner
from agent_memory_orchestrator.reasoning_graph.central_merge.applier import repo_central_graph_path
from agent_memory_orchestrator.reasoning_graph.jobs.constants import GRAPH_SCHEMA_VERSION
from agent_memory_orchestrator.reasoning_graph.jobs.constants import PIPELINE_VERSION
from agent_memory_orchestrator.reasoning_graph import GraphEmbeddingStore
from agent_memory_orchestrator.reasoning_graph import RetrievalDocument
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


class _NoGraphWalkStore:
    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, object]]:
        raise AssertionError("fast retrieval smoke should not expand graph neighbors")

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, object]]:
        raise AssertionError("fast retrieval smoke should not load graph nodes")


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


def test_retrieval_index_store_migrates_old_fts_schema_without_packet_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "retrieval.sqlite"
    conn = connect(db_path)
    conn.execute(
        """
        CREATE TABLE retrieval_documents (
          doc_id TEXT PRIMARY KEY,
          doc_type TEXT NOT NULL,
          graph_node_id TEXT NOT NULL,
          node_kind TEXT NOT NULL,
          title TEXT NOT NULL,
          body TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE VIRTUAL TABLE retrieval_documents_fts USING fts5(
          doc_id UNINDEXED,
          title,
          body
        )
        """
    )
    conn.commit()

    index_store = RetrievalIndexStore(conn)
    docs = build_retrieval_documents_from_graph(_graph(), session_id="s1")
    written = index_store.replace_documents(docs)
    columns = [row["name"] for row in conn.execute("PRAGMA table_info(retrieval_documents_fts)").fetchall()]
    hits = index_store.bm25_search("WP0001 abc1234 retrieval", limit=5)

    assert written == len(docs)
    assert columns == ["doc_id", "title", "body", "packet_id", "commit_sha", "node_kind", "memory_class"]
    assert hits


def test_retrieval_document_build_can_filter_to_production_graph_schema() -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:legacy",
            kind="ReasoningNode",
            label="legacy graph delta node",
            summary="Old legacy graph output should not enter production retrieval docs.",
            session_id="s1",
        )
    )
    graph.upsert_node(
        GraphNode(
            id="reason:v2",
            kind="ReasoningNode",
            label="v2 reasoning node",
            summary="Production answer-grade reasoning node.",
            session_id="s1",
            metadata={
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "packet_id": "WP0001",
            },
        )
    )

    docs = build_retrieval_documents_from_graph(
        graph,
        session_id="s1",
        pipeline_version=PIPELINE_VERSION,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
    )

    assert [doc.graph_node_id for doc in docs] == ["reason:v2"]


def test_retrieval_documents_and_search_are_repo_scoped(tmp_path: Path) -> None:
    graph = InMemoryGraphStore()
    for repo_id, commit_id, topic in (
        ("repo:amo", "g-amo", "central active memory for AMO retrieval"),
        ("repo:dora", "g-dora", "central active memory for Dora advisory"),
    ):
        graph.upsert_node(
            GraphNode(
                id=f"kver:{repo_id}",
                kind="KnowledgeVersion",
                label=topic,
                summary=topic,
                status="active",
                scope="central",
                metadata={
                    "atom_kind": "code_region",
                    "graph_commit_id": commit_id,
                    "repo_id": repo_id,
                    "pipeline_version": PIPELINE_VERSION,
                    "graph_schema_version": GRAPH_SCHEMA_VERSION,
                },
            )
        )
        graph.upsert_node(
            GraphNode(
                id=f"view:{repo_id}",
                kind="GraphView",
                label=f"{repo_id} active view",
                summary=f"{repo_id} active view",
                status="active",
                scope="central",
                metadata={
                    "branch": "main",
                    "mode": "active",
                    "graph_commit_id": commit_id,
                    "repo_id": repo_id,
                    "pipeline_version": PIPELINE_VERSION,
                    "graph_schema_version": GRAPH_SCHEMA_VERSION,
                },
            )
        )
    docs = build_retrieval_documents_from_graph(
        graph,
        pipeline_version=PIPELINE_VERSION,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
    )
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(docs)

    assert {doc.repo_id for doc in docs if doc.doc_type == "central_version"} == {"repo:amo", "repo:dora"}
    assert [doc.graph_node_id for doc in index_store.list_documents(repo_id="repo:amo")] == ["kver:repo:amo", "view:repo:amo"]

    result = retrieve_session_graph(
        query="central active memory",
        index_store=index_store,
        graph_store=graph,
        repo_id="repo:dora",
        limit=5,
        expand_neighbors=0,
    )

    assert result.hits
    assert {hit.document.repo_id for hit in result.hits} == {"repo:dora"}
    assert all("amo" not in hit.document.body.lower() for hit in result.hits)


def test_offline_index_only_retrieval_respects_repo_scope(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="legacy-code",
                    doc_type="code",
                    graph_node_id="legacy:code",
                    node_kind="CodeNode",
                    repo_id="",
                    packet_id="WP0001",
                    commit_sha="abc123",
                    title="graph_service.py legacy raw If",
                    body="why graph_service.py changed legacy raw CodeNode",
                ),
            ]
        )
        repo_doc = RetrievalDocument(
            doc_id="repo-file-impact",
            doc_type="file_impact",
            graph_node_id="file-impact:graph_service.py",
            node_kind="FileImpactSummary",
            repo_id="repo:amo",
            packet_id="WP0002",
            commit_sha="def456",
            title="graph_service.py file impact",
            body="why graph_service.py changed through curated file impact summary",
        )
        projection_id = "rproj:repo-amo"
        index_store.upsert_projection(
            projection_id=projection_id,
            repo_id="repo:amo",
            projection_version="test",
            source_artifact_hash="source",
            doc_content_hash="content",
            status="building",
        )
        index_store.replace_projection_documents([repo_doc], repo_id="repo:amo", projection_id=projection_id)
        index_store.activate_projection(repo_id="repo:amo", projection_id=projection_id)

        result = _retrieve_index_only(
            settings,
            SimpleNamespace(
                db_path=tmp_path / "retrieval.sqlite",
                query="why did we change graph_service.py?",
                repo_id="repo:amo",
                session_id="",
                limit=5,
                graph_scope="",
            ),
        )

        hits = result["retrieval"]["hits"]
        assert hits
        assert {hit["document"]["repo_id"] for hit in hits} == {"repo:amo"}
        assert all(hit["document"]["node_kind"] != "CodeNode" for hit in hits)
    finally:
        conn.close()


def test_activating_projection_retires_prior_repo_projection(tmp_path: Path) -> None:
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        for projection_id in ("rproj:old", "rproj:new"):
            index_store.upsert_projection(
                projection_id=projection_id,
                repo_id="repo:amo",
                projection_version="test",
                source_artifact_hash=projection_id,
                doc_content_hash=projection_id,
                status="validated",
            )

        index_store.activate_projection(repo_id="repo:amo", projection_id="rproj:old")
        index_store.activate_projection(repo_id="repo:amo", projection_id="rproj:new")

        assert index_store.active_projection_id("repo:amo") == "rproj:new"
        assert index_store.projection("rproj:new")["status"] == "active"
        assert index_store.projection("rproj:old")["status"] == "historical"
    finally:
        conn.close()


def test_offline_index_only_requires_active_projection_for_repo_scope(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="repo-stale-code",
                    doc_type="session_codenode",
                    graph_node_id="code:if",
                    node_kind="CodeNode",
                    repo_id="repo:amo",
                    packet_id="WP0001",
                    commit_sha="abc123",
                    title="stale raw code",
                    body="why graph_service.py changed stale raw CodeNode",
                )
            ]
        )

        result = _retrieve_index_only(
            settings,
            SimpleNamespace(
                db_path=tmp_path / "retrieval.sqlite",
                query="why did we change graph_service.py?",
                repo_id="repo:amo",
                session_id="",
                limit=5,
                graph_scope="",
            ),
        )

        assert result["ok"] is False
        assert result["error"] == "active_projection_missing"
    finally:
        conn.close()


def test_offline_graph_retrieve_with_repo_id_uses_repo_central_graph(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    settings = _settings(tmp_path)
    opened_paths: list[tuple[Path, bool]] = []
    service_paths: list[Path] = []
    service_stores: list[object] = []
    service_read_only: list[bool] = []

    class CapturingStore:
        def __init__(self, graph_path: Path, *, read_only: bool = False) -> None:
            opened_paths.append((graph_path, read_only))

    class CapturingService:
        def __init__(
            self,
            service_settings: Settings,
            *,
            store: object | None = None,
            read_only: bool = False,
            **_: object,
        ) -> None:
            service_paths.append(service_settings.graph_path)
            service_stores.append(store)
            service_read_only.append(read_only)

        def retrieve_indexed_graph(self, **kwargs: object) -> dict[str, object]:
            return {"ok": True, "repo_id": kwargs.get("repo_id"), "graph_path": str(service_paths[-1])}

        def close(self) -> None:
            return None

    monkeypatch.setattr(graph_cli_module.Settings, "load", staticmethod(lambda: settings))
    monkeypatch.setattr(graph_cli_module, "KuzuGraphStore", CapturingStore)
    monkeypatch.setattr(graph_cli_module, "GraphRagService", CapturingService)

    exit_code = cli_module.main(
        [
            "graph-retrieve",
            "--query",
            "why did graph_service.py change?",
            "--repo-id",
            "repo:amo",
            "--no-vector",
            "--offline",
        ]
    )

    expected_path = repo_central_graph_path(settings, "repo:amo")
    assert exit_code == 0
    assert opened_paths == [(expected_path, True)]
    assert service_paths == [expected_path]
    assert service_read_only == [True]
    assert service_stores
    assert service_stores[0] is not None
    assert json.loads(capsys.readouterr().out)["graph_path"] == str(expected_path)


def _central_graph() -> InMemoryGraphStore:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:job1:WP0001:decision:retrieval",
            kind="ReasoningNode",
            label="Decision: retrieval projection",
            summary="Session support says retrieval should use central active memory with packet and code trace.",
            status="accepted",
            session_id="s1",
            commit_id="abc1234",
            metadata={
                "packet_id": "WP0001",
                "commit_sha": "abc1234",
                "node_type": "Decision",
                "statement": "Retrieval should use central active memory with packet and code trace.",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="kver:active-retrieval",
            kind="KnowledgeVersion",
            label="Active retrieval projection version",
            summary="Active central memory: retrieval defaults to GraphView main active and keeps session support for traces.",
            status="active",
            scope="central",
            session_id="s1",
            metadata={
                "atom_id": "katom:retrieval",
                "atom_kind": "code_region",
                "canonical_key": "code_region|repo:amo|src/agent_memory_orchestrator/reasoning_graph/retrieval.py||retrieve_session_graph",
                "graph_commit_id": "g2",
                "merge_plan_id": "plan:g2",
                "repo_id": "repo:amo",
                "source_node_ids": ["reason:job1:WP0001:decision:retrieval"],
                "status": "active",
                "version_metadata": {"file_path": "src/agent_memory_orchestrator/reasoning_graph/retrieval.py"},
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="katom:retrieval",
            kind="KnowledgeAtom",
            label="retrieve_session_graph canonical atom",
            summary="Canonical code region for retrieval active memory.",
            status="active",
            scope="central",
            session_id="s1",
            metadata={
                "atom_kind": "code_region",
                "canonical_key": "code_region|repo:amo|src/agent_memory_orchestrator/reasoning_graph/retrieval.py||retrieve_session_graph",
                "canonical_key_version": 1,
                "graph_commit_id": "g2",
                "repo_id": "repo:amo",
                "source_node_ids": ["reason:job1:WP0001:decision:retrieval"],
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="kver:old-retrieval",
            kind="KnowledgeVersion",
            label="Old retrieval projection version",
            summary="Old central memory that should not leak into the active graph view.",
            status="superseded",
            scope="central",
            session_id="s1",
            metadata={
                "atom_id": "katom:retrieval",
                "atom_kind": "code_region",
                "canonical_key": "code_region|repo:amo|old",
                "graph_commit_id": "g1",
                "repo_id": "repo:amo",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="g2",
            kind="GraphCommit",
            label="g2",
            summary="Applied exact central atoms for retrieval.",
            status="applied",
            scope="central",
            session_id="s1",
            metadata={
                "branch": "main",
                "graph_commit_id": "g2",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="v2view:main:active",
            kind="GraphView",
            label="main/active",
            summary="Active graph view at g2.",
            status="active",
            scope="central",
            session_id="s1",
            metadata={
                "branch": "main",
                "mode": "active",
                "graph_commit_id": "g2",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_edge(
        GraphEdge(
            id="edge:kver-session",
            source_id="kver:active-retrieval",
            target_id="reason:job1:WP0001:decision:retrieval",
            kind="DERIVED_FROM_SESSION_NODE",
        )
    )
    return graph


def test_retrieval_documents_use_active_central_graph_view_with_session_support() -> None:
    docs = build_retrieval_documents_from_graph(
        _central_graph(),
        session_id="s1",
        pipeline_version=PIPELINE_VERSION,
        graph_schema_version=GRAPH_SCHEMA_VERSION,
    )

    by_id = {doc.graph_node_id: doc for doc in docs}

    assert by_id["kver:active-retrieval"].doc_type == "central_version"
    assert by_id["kver:active-retrieval"].memory_class == "central_active_memory"
    assert by_id["katom:retrieval"].doc_type == "central_atom"
    assert by_id["reason:job1:WP0001:decision:retrieval"].doc_type == "reasoning"
    assert "kver:old-retrieval" not in by_id


def test_retrieve_session_graph_prefers_active_central_version(tmp_path: Path) -> None:
    graph = _central_graph()
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(
        build_retrieval_documents_from_graph(
            graph,
            session_id="s1",
            pipeline_version=PIPELINE_VERSION,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
        )
    )

    result = retrieve_session_graph(
        query="why does retrieval use central active memory with packet code trace?",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=3,
        expand_neighbors=3,
    )

    assert result.hits
    assert result.hits[0].document.graph_node_id == "kver:active-retrieval"
    assert result.hits[0].document.doc_type == "central_version"
    assert any(reason.startswith("central_active_boost:") for reason in result.hits[0].reasons)
    assert any(neighbor["id"] == "reason:job1:WP0001:decision:retrieval" for neighbor in result.hits[0].neighbors)


def test_version_flow_query_prefers_active_central_version_for_locator(tmp_path: Path) -> None:
    graph = _central_graph()
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(
        build_retrieval_documents_from_graph(
            graph,
            session_id="s1",
            pipeline_version=PIPELINE_VERSION,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
        )
    )

    result = retrieve_session_graph(
        query="show version flow for src/agent_memory_orchestrator/reasoning_graph/retrieval.py::retrieve_session_graph",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=3,
        expand_neighbors=0,
    )

    assert result.intent == "version_flow"
    assert result.hits[0].document.graph_node_id == "kver:active-retrieval"
    assert any(reason == "central_active_boost:0.55" for reason in result.hits[0].reasons)


def test_central_retrieval_includes_active_versions_from_prior_graph_commits() -> None:
    graph = InMemoryGraphStore()
    repo_id = "repo:test"
    graph.upsert_node(
        GraphNode(
            id="v2view:repo_test:main:active",
            kind="GraphView",
            label="main active",
            status="active",
            metadata={"repo_id": repo_id, "branch": "main", "mode": "active", "graph_commit_id": "gcommit:new"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="kver:decision:old-active",
            kind="KnowledgeVersion",
            label="Graph retrieval design",
            summary="Use central active GraphView retrieval for graph queries.",
            status="active",
            metadata={
                "repo_id": repo_id,
                "atom_kind": "decision",
                "status": "superseded",
                "graph_commit_id": "gcommit:older",
                "version_metadata": {
                    "subject": "Graph retrieval design",
                    "statement": "Use central active GraphView retrieval for graph queries.",
                },
            },
        )
    )

    docs = build_retrieval_documents_from_graph(graph, repo_id=repo_id)

    central_versions = [doc for doc in docs if doc.doc_type == "central_version"]
    assert [doc.graph_node_id for doc in central_versions] == ["kver:decision:old-active"]
    assert central_versions[0].title == "Decision: Graph retrieval design"
    assert "statement:" in central_versions[0].body
    assert "source: active central memory" in central_versions[0].body


def test_generic_query_does_not_overboost_low_overlap_exact_central_version(tmp_path: Path) -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="kver:debug-html",
            kind="KnowledgeVersion",
            label="debug extraction html",
            summary="A debug extraction HTML file was done.",
            status="active",
            scope="central",
            session_id="s1",
            metadata={
                "atom_id": "katom:debug-html",
                "atom_kind": "file",
                "canonical_key": "file|repo:amo|scraper_lean/debug/3_extraction_done.html",
                "graph_commit_id": "g2",
                "repo_id": "repo:amo",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="v2view:main:active",
            kind="GraphView",
            label="main/active",
            summary="Active graph view at g2.",
            status="active",
            scope="central",
            session_id="s1",
            metadata={
                "branch": "main",
                "mode": "active",
                "graph_commit_id": "g2",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="code:advisory-nav",
            kind="CodeNode",
            label="backend/app/workers/advisory_worker.py::city_names",
            summary="Implemented advisory navigation data flow and the navigation tab route.",
            status="active",
            session_id="s1",
            metadata={
                "packet_id": "WP0009",
                "commit_sha": "8f5b85c",
                "file_path": "backend/app/workers/advisory_worker.py",
                "symbol": "city_names",
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
            },
        )
    )
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(
        build_retrieval_documents_from_graph(
            graph,
            session_id="s1",
            pipeline_version=PIPELINE_VERSION,
            graph_schema_version=GRAPH_SCHEMA_VERSION,
        )
    )

    result = retrieve_session_graph(
        query="what work was done for advisory and navigation?",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=2,
        expand_neighbors=0,
    )

    assert result.hits[0].document.graph_node_id == "code:advisory-nav"
    assert any(
        "central_low_topic_overlap_penalty" in hit.reasons
        for hit in result.hits
        if hit.document.graph_node_id == "kver:debug-html"
    )


def test_code_query_does_not_overboost_weakly_related_central_decision(tmp_path: Path) -> None:
    graph = InMemoryGraphStore()
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(
        [
            RetrievalDocument(
                doc_id="doc:central-slack",
                doc_type="central_version",
                graph_node_id="kver:decision:slack",
                node_kind="KnowledgeVersion",
                repo_id="repo:amo",
                title="Decision: Can now answer Slack mentions",
                body="kind: KnowledgeVersion\natom_kind: decision\nsummary: AMO web memory can answer Slack mentions.",
                metadata={"node_metadata": {"atom_kind": "decision", "status": "active"}},
                memory_class="central_active_memory",
                importance=0.95,
                packet_id="",
                commit_sha="",
            ),
            RetrievalDocument(
                doc_id="doc:file-amo-js",
                doc_type="file_impact",
                graph_node_id="fileimpact:amo-js",
                node_kind="FileImpactSummary",
                repo_id="repo:amo",
                title="Impact summary for src/agent_memory_orchestrator/web/amo.js",
                body="AMO control web UI changed in the graph workbench implementation.",
                metadata={"source": "curated_graph_manifest"},
                memory_class="file_impact_summary",
                importance=0.84,
                packet_id="WP0015",
                commit_sha="bdf1c3f",
            ),
        ]
    )

    result = retrieve_session_graph(
        query="what changed for AMO control room web UI?",
        index_store=index_store,
        graph_store=graph,
        repo_id="repo:amo",
        limit=2,
        expand_neighbors=0,
    )

    assert result.intent == "code_why"
    assert result.hits[0].document.doc_type == "file_impact"
    assert result.hits[0].document.graph_node_id == "fileimpact:amo-js"
    assert any("central_low_topic_overlap_penalty" in hit.reasons for hit in result.hits if hit.document.doc_type == "central_version")


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

    assert result.intent == "decision_history"
    assert result.vector_status == "sqlite:completed"
    assert result.reranker == "deterministic+bi_encoder"
    assert result.candidate_counts["bm25"] > 0
    assert result.candidate_counts["vector"] > 0
    assert result.hits[0].document.doc_type == "reasoning"
    assert result.hits[0].document.graph_node_id == "reason:WP0001:decision:retrieval"
    assert any(reason.startswith("bi_encoder_score:") for reason in result.hits[0].reasons)
    assert any(neighbor["id"] == "code:retrieval:retrieve_session_graph" for neighbor in result.hits[0].neighbors)


def test_retrieve_session_graph_can_skip_graph_walk_for_fast_smoke(tmp_path: Path) -> None:
    graph = _graph()
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(build_retrieval_documents_from_graph(graph, session_id="s1"))

    result = retrieve_session_graph(
        query="why use BM25 vector retrieval before graph expansion",
        index_store=index_store,
        graph_store=_NoGraphWalkStore(),
        session_id="s1",
        limit=2,
        expand_neighbors=0,
        include_graph_nodes=False,
    )

    assert result.hits
    assert result.hits[0].neighbors == ()
    assert result.hits[0].graph_node == {}


def test_retrieve_session_graph_can_require_bi_encoder_candidates(tmp_path: Path) -> None:
    graph = _graph()
    _conn, index_store, embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(build_retrieval_documents_from_graph(graph, session_id="s1"))

    with pytest.raises(ValueError, match="vector retrieval required"):
        retrieve_session_graph(
            query="why use BM25 vector retrieval before graph expansion",
            index_store=index_store,
            graph_store=graph,
            embedding_store=embedding_store,
            embedder=_KeywordEmbedder(),
            embedding_model="test-embedder",
            graph_scope="test-graph",
            session_id="s1",
            limit=3,
            require_vector=True,
        )


def test_retrieve_session_graph_applies_cross_encoder_rerank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    calls: dict[str, object] = {}

    def fake_rerank_candidates(**kwargs):
        candidates = list(kwargs["candidates"])
        calls["backend"] = kwargs["backend"]
        calls["model_name"] = kwargs["model_name"]
        calls["candidate_count"] = len(candidates)
        return SimpleNamespace(
            scores={candidate.memory_id: 1.0 for candidate in candidates},
            backend="cross-encoder",
            model=kwargs["model_name"],
            fallback_reason="",
        )

    monkeypatch.setattr(
        "agent_memory_orchestrator.reasoning_graph.retrieval.rerank_candidates",
        fake_rerank_candidates,
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
        reranker_backend="cross-encoder",
        reranker_model="test-cross-encoder",
        rerank_top_k=2,
    )

    assert calls == {"backend": "cross-encoder", "model_name": "test-cross-encoder", "candidate_count": 2}
    assert result.reranker == "deterministic+bi_encoder+cross_encoder"
    assert any(reason.startswith("cross_encoder_score:") for reason in result.hits[0].reasons)
    assert any(reason == "cross_encoder_model:test-cross-encoder" for reason in result.hits[0].reasons)


def test_decision_history_query_prefers_primary_topic_over_metadata_noise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:fix:capture_only",
            kind="ReasoningNode",
            label="Fix: Hook behavior change to capture-only",
            summary="Hooks are capture-only and explicit MCP graph search performs retrieval.",
            status="accepted",
            session_id="s1",
            commit_id="8351639",
            metadata={
                "packet_id": "WP0018",
                "commit_sha": "8351639",
                "node_type": "Fix",
                "subject": "Hook behavior change to capture-only",
                "statement": "Hooks are capture-only and explicit MCP graph search performs retrieval.",
                "reason": "Real hook execution timed out when retrieval and ingestion ran inside the hook.",
                "paths": ["src/agent_memory_orchestrator/hook.py"],
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="reason:memory:decision:splitting",
            kind="ReasoningNode",
            label="Decision: memory splitting strategy",
            summary="Store one durable idea per memory unit.",
            status="accepted",
            session_id="s1",
            commit_id="c78d93c",
            metadata={
                "packet_id": "WP0011",
                "commit_sha": "c78d93c",
                "node_type": "Decision",
                "subject": "memory splitting strategy",
                "statement": "Store one durable idea per memory unit.",
                "reason": "Mixed paragraph memories were too broad.",
                "paths": [
                    "README.md",
                    "docs/IMPLEMENTATION_TRACKER.md",
                    "src/agent_memory_orchestrator/hook.py",
                ],
            },
        )
    )
    _conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    index_store.upsert_documents(build_retrieval_documents_from_graph(graph, session_id="s1"))

    result = retrieve_session_graph(
        query="what decisions were made about Codex hooks?",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=2,
        expand_neighbors=0,
    )

    assert result.intent == "decision_history"
    assert result.hits[0].document.graph_node_id == "reason:hooks:fix:capture_only"
    assert any(reason.startswith("topic_focus_overlap:") for reason in result.hits[0].reasons)
    assert "topic_focus_penalty" in result.hits[1].reasons

    def misleading_cross_encoder(**kwargs):
        candidates = list(kwargs["candidates"])
        return SimpleNamespace(
            scores={
                candidate.memory_id: (
                    1.0 if "memory:decision:splitting" in candidate.memory_id else 0.0
                )
                for candidate in candidates
            },
            backend="cross-encoder",
            model=kwargs["model_name"],
            fallback_reason="",
        )

    monkeypatch.setattr(
        "agent_memory_orchestrator.reasoning_graph.retrieval.rerank_candidates",
        misleading_cross_encoder,
    )

    reranked = retrieve_session_graph(
        query="what decisions were made about Codex hooks?",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=2,
        expand_neighbors=0,
        reranker_backend="cross-encoder",
        reranker_model="misleading-cross-encoder",
        rerank_top_k=2,
    )

    assert reranked.reranker == "deterministic+cross_encoder"
    assert reranked.hits[0].document.graph_node_id == "reason:hooks:fix:capture_only"
    assert any(reason == "cross_encoder_weight:0.08" for reason in reranked.hits[1].reasons)


def test_version_flow_queries_boost_symbol_or_code_docs(tmp_path: Path) -> None:
    graph = _graph()
    graph.upsert_node(
        GraphNode(
            id="symbol:graph_service:version_flow",
            kind="Symbol",
            label="src/agent_memory_orchestrator/graph/service.py::GraphRagService.version_flow",
            summary="Symbol version_flow in graph service.",
            status="accepted",
            session_id="s1",
            commit_id="def5678",
            metadata={
                "file_path": "src/agent_memory_orchestrator/graph/service.py",
                "symbol": "GraphRagService.version_flow",
                "version_count": 1,
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="symbol:graph_service:rank_nodes",
            kind="Symbol",
            label="src/agent_memory_orchestrator/graph_service.py::_rank_nodes",
            summary="Symbol _rank_nodes in graph service.",
            status="accepted",
            session_id="s1",
            commit_id="def5678",
            metadata={
                "file_path": "src/agent_memory_orchestrator/graph_service.py",
                "symbol": "_rank_nodes",
                "version_count": 4,
            },
        )
    )
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
    assert classify_query("show version flow for graph service rank nodes") == "version_flow"
    assert classify_query("why did we add retrieval.py for graph expansion") == "code_why"
    assert classify_query("why did we add the daemon-owned Kuzu work ledger?") == "decision_history"
    assert classify_query("what changed for spatial graph controls?") == "code_why"
    assert classify_query("what changed over time for graph_service.py?") == "version_flow"
    assert result.hits
    assert result.hits[0].document.doc_type in {"symbol", "code"}

    natural_result = retrieve_session_graph(
        query="show version flow for graph service rank nodes",
        index_store=index_store,
        graph_store=graph,
        session_id="s1",
        limit=3,
        expand_neighbors=0,
    )

    assert natural_result.intent == "version_flow"
    assert natural_result.hits[0].document.graph_node_id == "symbol:graph_service:rank_nodes"


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
    assert "Answer from repository memory" in result["answer"]["text"]
    assert "Support: session context, commit-backed, code-linked" in result["answer"]["text"]
    assert "WP0001" not in result["answer"]["text"]
    first_citation = result["answer"]["citations"][0]
    assert first_citation["packet_ids"] == ["WP0001"]
    assert first_citation["commit_shas"] == ["abc1234"]
    assert "code:retrieval:retrieve_session_graph" in first_citation["code_node_ids"]


def test_code_locator_query_penalizes_unrelated_add_file_impacts(tmp_path: Path) -> None:
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="doc:demo:file",
                    doc_type="file_impact",
                    graph_node_id="fileimpact:demo",
                    node_kind="FileImpactSummary",
                    repo_id="repo:amo",
                    packet_id="WP0001",
                    commit_sha="7ea1d74",
                    title="Impact summary for src/agent_memory_orchestrator/demo_marker.py",
                    body='Add demo_marker_message returning "amo demo memory marker".',
                    metadata={"path": "src/agent_memory_orchestrator/demo_marker.py"},
                ),
                RetrievalDocument(
                    doc_id="doc:peer:file",
                    doc_type="file_impact",
                    graph_node_id="fileimpact:peer",
                    node_kind="FileImpactSummary",
                    repo_id="repo:amo",
                    packet_id="WP0002",
                    commit_sha="8df94c2",
                    title="Impact summary for src/agent_memory_orchestrator/peer/service.py",
                    body="Add peer transport behavior for libp2p room messages.",
                    metadata={"path": "src/agent_memory_orchestrator/peer/service.py"},
                ),
            ]
        )

        result = retrieve_session_graph(
            query="why did we add demo_marker_message?",
            index_store=index_store,
            graph_store=_NoGraphWalkStore(),
            repo_id="repo:amo",
            limit=2,
            expand_neighbors=0,
            include_graph_nodes=False,
        )
    finally:
        conn.close()

    assert result.hits
    assert result.hits[0].document.doc_id == "doc:demo:file"
    peer_hit = next(hit for hit in result.hits if hit.document.doc_id == "doc:peer:file")
    assert "code_locator_mismatch_penalty" in peer_hit.reasons


def test_version_flow_query_prefers_matching_file_impact_rollup(tmp_path: Path) -> None:
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="doc:central:file-version",
                    doc_type="central_version",
                    graph_node_id="kver:file:demo-marker",
                    node_kind="KnowledgeVersion",
                    repo_id="repo:amo",
                    packet_id="",
                    commit_sha="",
                    title="File version: src/agent_memory_orchestrator/demo_marker.py",
                    body="kind: KnowledgeVersion\natom_kind: file\nfile_path: src/agent_memory_orchestrator/demo_marker.py",
                    metadata={"node_metadata": {"atom_kind": "file"}},
                ),
                RetrievalDocument(
                    doc_id="doc:file-impact:demo-marker",
                    doc_type="file_impact",
                    graph_node_id="fileimpact:demo-marker",
                    node_kind="FileImpactSummary",
                    repo_id="repo:amo",
                    packet_id="WP0001",
                    commit_sha="7ea1d74",
                    title="Impact summary for src/agent_memory_orchestrator/demo_marker.py",
                    body="demo_marker.py was touched by two curated code impacts.",
                    metadata={
                        "path": "src/agent_memory_orchestrator/demo_marker.py",
                        "commit_shas": ["7ea1d74", "ac2e1db"],
                        "commit_messages": ["demo(amo): add demo marker function", "demo(amo): update marker after retrieval fix"],
                        "reasons": [
                            "Add demo_marker_message for the AMO memory capture demo.",
                            "Update the marker after the retrieval fix on the demo branch.",
                        ],
                    },
                ),
                RetrievalDocument(
                    doc_id="doc:packet:unrelated",
                    doc_type="packet",
                    graph_node_id="WP0099",
                    node_kind="Packet",
                    repo_id="repo:amo",
                    packet_id="WP0099",
                    commit_sha="9bad999",
                    title="WP0099 fix(retrieval): unrelated version warning",
                    body="Unrelated retrieval version warning text.",
                ),
            ]
        )

        result = retrieve_session_graph(
            query="how did demo_marker.py evolve over time",
            index_store=index_store,
            graph_store=_NoGraphWalkStore(),
            repo_id="repo:amo",
            limit=3,
            expand_neighbors=0,
            include_graph_nodes=False,
        )
    finally:
        conn.close()

    assert result.intent == "version_flow"
    assert result.hits[0].document.doc_id == "doc:file-impact:demo-marker"
    assert "version_file_impact_boost" in result.hits[0].reasons
    assert "version_locator_file_rollup_boost" in result.hits[0].reasons


def test_code_why_query_prefers_impact_over_central_file_stub(tmp_path: Path) -> None:
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="doc:central:file-version",
                    doc_type="central_version",
                    graph_node_id="kver:file:graph-service",
                    node_kind="KnowledgeVersion",
                    repo_id="repo:amo",
                    packet_id="",
                    commit_sha="",
                    title="File version: src/agent_memory_orchestrator/graph_service.py",
                    body="kind: KnowledgeVersion\natom_kind: file\nfile_path: src/agent_memory_orchestrator/graph_service.py",
                    metadata={"node_metadata": {"atom_kind": "file"}},
                    memory_class="central_active_memory",
                    importance=0.95,
                ),
                RetrievalDocument(
                    doc_id="doc:impact:graph-service",
                    doc_type="code_impact",
                    graph_node_id="impact:graph-service",
                    node_kind="CodeImpactSummary",
                    repo_id="repo:amo",
                    packet_id="WP0004",
                    commit_sha="37943f2",
                    title="Code impact for graph_service.py",
                    body="graph_service.py changed to route graph retrieval through curated packet-backed impact summaries.",
                    metadata={"selected_files": ["src/agent_memory_orchestrator/graph_service.py"]},
                    memory_class="code_impact_summary",
                    importance=0.82,
                ),
            ]
        )

        result = retrieve_session_graph(
            query="why did we change graph_service.py?",
            index_store=index_store,
            graph_store=_NoGraphWalkStore(),
            repo_id="repo:amo",
            limit=2,
            expand_neighbors=0,
            include_graph_nodes=False,
        )
    finally:
        conn.close()

    assert result.intent == "code_why"
    assert result.hits[0].document.doc_id == "doc:impact:graph-service"
    central_hit = next(hit for hit in result.hits if hit.document.doc_id == "doc:central:file-version")
    assert "central_active_boost:0.12" in central_hit.reasons


def test_code_why_query_treats_query_echo_packet_as_support(tmp_path: Path) -> None:
    conn, index_store, _embedding_store = _sqlite_store(tmp_path)
    try:
        index_store.upsert_documents(
            [
                RetrievalDocument(
                    doc_id="doc:packet:query-echo",
                    doc_type="packet",
                    graph_node_id="WP0063",
                    node_kind="Packet",
                    repo_id="repo:amo",
                    packet_id="WP0063",
                    commit_sha="9b74876",
                    title="WP0063 feat(retrieval): add typed answer trace traversal",
                    body="User asked: why did we change graph_service.py? The packet records the question.",
                    memory_class="work_packet",
                    importance=0.5,
                ),
                RetrievalDocument(
                    doc_id="doc:impact:graph-service",
                    doc_type="code_impact",
                    graph_node_id="impact:graph-service",
                    node_kind="CodeImpactSummary",
                    repo_id="repo:amo",
                    packet_id="WP0063",
                    commit_sha="9b74876",
                    title="Code impact for graph_service.py",
                    body="graph_service.py changed to add typed answer trace traversal for packet-backed retrieval explanations.",
                    metadata={"selected_files": ["src/agent_memory_orchestrator/graph_service.py"]},
                    memory_class="code_impact_summary",
                    importance=0.82,
                ),
            ]
        )

        result = retrieve_session_graph(
            query="why did we change graph_service.py?",
            index_store=index_store,
            graph_store=_NoGraphWalkStore(),
            repo_id="repo:amo",
            limit=2,
            expand_neighbors=0,
            include_graph_nodes=False,
            reranker_backend="lexical",
        )
    finally:
        conn.close()

    assert result.reranker == "deterministic+lexical"
    assert result.hits[0].document.doc_id == "doc:impact:graph-service"
    packet_hit = next(hit for hit in result.hits if hit.document.doc_id == "doc:packet:query-echo")
    assert "packet_support_penalty" in packet_hit.reasons


def test_answer_renderer_builds_version_timeline_from_file_impact() -> None:
    result = {
        "query": "what was the demo_marker function about?",
        "hits": [
            {
                "document": RetrievalDocument(
                    doc_id="doc:file-impact:demo-marker",
                    doc_type="file_impact",
                    graph_node_id="fileimpact:demo-marker",
                    node_kind="FileImpactSummary",
                    repo_id="repo:amo",
                    packet_id="WP0001",
                    commit_sha="7ea1d74",
                    title="Impact summary for src/agent_memory_orchestrator/demo_marker.py",
                    body="demo_marker.py was touched by two curated code impacts.",
                    metadata={
                        "path": "src/agent_memory_orchestrator/demo_marker.py",
                        "selected_files": ["src/agent_memory_orchestrator/demo_marker.py", "tests/test_demo_marker.py"],
                        "commit_shas": ["7ea1d74", "ac2e1db"],
                        "commit_messages": ["demo(amo): add demo marker function", "demo(amo): update marker after retrieval fix"],
                        "packet_ids": ["WP0001", "WP0002"],
                        "reasons": [
                            "Add demo_marker_message returning the AMO demo memory marker and validate it with a focused test.",
                            "Update the marker after the retrieval fix on the demo branch.",
                        ],
                    },
                ).as_dict(),
                "score": 1.2,
            },
            {
                "document": RetrievalDocument(
                    doc_id="doc:file-version:demo-marker",
                    doc_type="central_version",
                    graph_node_id="kver:file:demo-marker",
                    node_kind="KnowledgeVersion",
                    repo_id="repo:amo",
                    packet_id="",
                    commit_sha="",
                    title="File version: src/agent_memory_orchestrator/demo_marker.py",
                    body="kind: KnowledgeVersion\natom_kind: file\nfile_path: src/agent_memory_orchestrator/demo_marker.py",
                    metadata={"node_metadata": {"atom_kind": "file"}},
                ).as_dict(),
                "score": 1.1,
            },
            {
                "document": RetrievalDocument(
                    doc_id="doc:decision:update",
                    doc_type="central_version",
                    graph_node_id="kver:decision:update",
                    node_kind="KnowledgeVersion",
                    repo_id="repo:amo",
                    packet_id="",
                    commit_sha="",
                    title="Decision: Commit to demo branch",
                    body="statement: Commit to demo branch to address the demo marker function after retrieval fix.",
                    metadata={
                        "node_metadata": {
                            "atom_kind": "decision",
                            "version_metadata": {
                                "linked_commits": ["ac2e1db"],
                                "linked_files": ["src/agent_memory_orchestrator/demo_marker.py"],
                                "linked_packets": ["WP0002"],
                                "statement": "Commit to demo branch to address the demo marker function after retrieval fix.",
                            },
                        }
                    },
                ).as_dict(),
                "score": 1.0,
            },
        ],
    }

    answer = _answer_from_retrieval_result(result, graph_store=InMemoryGraphStore())
    text = answer["text"]

    assert "Version history for src/agent_memory_orchestrator/demo_marker.py:" in text
    assert "7ea1d74 demo(amo): add demo marker function" in text
    assert "ac2e1db demo(amo): update marker after retrieval fix" in text
    assert "Why: Add demo_marker_message returning the AMO demo memory marker" in text
    assert "File version: src/agent_memory_orchestrator/demo_marker.py" not in text
    assert "Support: file-impact summary, packet-backed" in text
    assert answer["context"]["version_timeline"]["commit_count"] == 2


def test_answer_renderer_preserves_packet_discussion_before_file_versions() -> None:
    result = {
        "query": "how is peer-to-peer communication configured, especially context management?",
        "hits": [
            {
                "document": RetrievalDocument(
                    doc_id="doc:packet",
                    doc_type="packet",
                    graph_node_id="job:WP0006",
                    node_kind="Packet",
                    repo_id="repo:amo",
                    packet_id="WP0006",
                    commit_sha="f22bc4c",
                    title="WP0006 docs(peer): promote peer-agent watcher flow",
                    body=(
                        "Packet: WP0006 docs(peer): promote peer-agent watcher flow\n"
                        "Analyze agent to agent communication with memory and context management across devices."
                    ),
                ).as_dict(),
                "score": 1.0,
            },
            {
                "document": RetrievalDocument(
                    doc_id="doc:file-version",
                    doc_type="central_version",
                    graph_node_id="kver:file:peer-context",
                    node_kind="KnowledgeVersion",
                    repo_id="repo:amo",
                    packet_id="",
                    commit_sha="",
                    title="File version: src/agent_memory_orchestrator/peer/context.py",
                    body="kind: KnowledgeVersion\natom_kind: file\nfile_path: src/agent_memory_orchestrator/peer/context.py",
                    metadata={"node_metadata": {"atom_kind": "file"}},
                ).as_dict(),
                "score": 0.99,
            },
        ],
    }

    answer = _answer_from_retrieval_result(result, graph_store=InMemoryGraphStore())
    text = answer["text"]

    assert "Relevant work and discussion:" in text
    assert "agent to agent communication with memory and context management" in text
    assert "Decisions and reasoning:" not in text
    assert "File version: src/agent_memory_orchestrator/peer/context.py" not in text
    assert answer["context"]["items"][0]["doc_type"] == "packet"


def test_graph_service_retrieve_uses_active_embedding_scope_when_unspecified(tmp_path: Path) -> None:
    svc = GraphRagService(
        _settings(tmp_path),
        store=_graph(),
        planner=DeterministicPlanner(),
    )
    try:
        svc.rebuild_retrieval_index(session_id="s1")
        svc.embed_retrieval_index(session_id="s1", limit=0, graph_scope="stage6_session_graph", rebuild_faiss=False)
        result = svc.retrieve_indexed_graph(
            query="why use BM25 vector retrieval before graph expansion",
            session_id="s1",
            limit=3,
            use_vector=True,
            require_vector=True,
        )
    finally:
        svc.close()

    assert result["ok"] is True
    assert result["graph_scope"] == "stage6_session_graph"
    assert result["retrieval"]["candidate_counts"]["vector"] > 0


def test_graph_service_retrieve_falls_back_from_stale_configured_embedding_scope(tmp_path: Path) -> None:
    settings = replace(_settings(tmp_path), retrieval_graph_scope="stage6_session_graph")
    svc = GraphRagService(
        settings,
        store=_graph(),
        planner=DeterministicPlanner(),
    )
    try:
        svc.rebuild_retrieval_index(session_id="s1")
        svc.embed_retrieval_index(session_id="s1", limit=0, graph_scope="v2", rebuild_faiss=False)
        result = svc.retrieve_indexed_graph(
            query="why use BM25 vector retrieval before graph expansion",
            session_id="s1",
            limit=3,
            use_vector=True,
            require_vector=True,
        )
    finally:
        svc.close()

    assert result["ok"] is True
    assert result["graph_scope"] == "v2"
    assert result["retrieval"]["candidate_counts"]["vector"] > 0


def test_answer_trace_walks_packet_commit_hunk_and_code_chain() -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:problem",
            kind="ReasoningNode",
            label="Problem: Hook execution timeout",
            summary="Codex hooks timed out when retrieval ran inside the hook.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639", "node_type": "Problem"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:fix",
            kind="ReasoningNode",
            label="Fix: Hook behavior change to capture-only",
            summary="Hooks became capture-only and retrieval moved to explicit graph search.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639", "node_type": "Fix"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="packet:WP0018",
            kind="Packet",
            label="WP0018",
            summary="GraphRAG hook pivot packet.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="commit:8351639",
            kind="Commit",
            label="8351639",
            summary="feat(graph): pivot memory runtime to Kuzu GraphRAG",
            commit_id="8351639",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="evidence:E01156",
            kind="EvidenceRef",
            label="rationale:E01156",
            summary="UserPromptSubmit is capture-only and no memory injection runs in the hook.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639", "evidence_ref_id": "E01156"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="hunk:hook",
            kind="CodeHunk",
            label="src/agent_memory_orchestrator/hook.py:40",
            summary="Changed hook response to avoid prompt injection.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="code:hook_response",
            kind="CodeNode",
            label="src/agent_memory_orchestrator/hook.py::_hook_response",
            summary="Builds capture-only hook response.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    for edge in [
        GraphEdge("e1", "reason:hooks:problem", "packet:WP0018", "REASON_NODE_IN_PACKET"),
        GraphEdge("e2", "reason:hooks:fix", "packet:WP0018", "REASON_NODE_IN_PACKET"),
        GraphEdge("e3", "reason:hooks:fix", "commit:8351639", "REASON_NODE_EXPLAINS_COMMIT"),
        GraphEdge("e4", "reason:hooks:fix", "evidence:E01156", "REASON_NODE_EVIDENCED_BY"),
        GraphEdge("e5", "commit:8351639", "hunk:hook", "COMMIT_PRODUCED_HUNK"),
        GraphEdge("e6", "hunk:hook", "code:hook_response", "HUNK_MAPS_TO_CODE_NODE"),
    ]:
        graph.upsert_edge(edge)

    trace = build_answer_trace(
        seed_node_id="reason:hooks:fix",
        graph_store=graph,
        query="what decisions were made about Codex hooks?",
    )
    trace_text = format_answer_trace(trace)

    assert [item["role"] for item in trace["chain"]] == ["Problem", "Fix"]
    assert trace["support"]["packet_ids"] == ["WP0018"]
    assert trace["support"]["commit_shas"] == ["8351639"]
    assert "evidence:E01156" in trace["support"]["evidence_ids"]
    assert "code:hook_response" in trace["support"]["code_node_ids"]
    assert "Problem: Codex hooks timed out" in trace_text
    assert "Fix: Hooks became capture-only" in trace_text


def test_central_answer_trace_contract_collects_active_support() -> None:
    trace = build_central_answer_trace(
        repo_id="repo:amo",
        graph_view={"view_id": "v2view:repo:amo:main:active", "graph_commit_id": "v2gcommit:1", "repo_id": "repo:amo"},
        graph_commit={"graph_commit_id": "v2gcommit:1"},
        central_versions=[{"version_id": "kver:file:graph_service"}],
        support_docs=[
            RetrievalDocument(
                doc_id="central-version",
                doc_type="central_version",
                graph_node_id="kver:file:graph_service",
                node_kind="KnowledgeVersion",
                packet_id="WP0018",
                commit_sha="8351639",
                title="graph_service.py version",
                body="graph_service.py active file version",
                repo_id="repo:amo",
                metadata={"path": "src/agent_memory_orchestrator/graph_service.py", "evidence_refs": ["E01156"]},
            ),
            RetrievalDocument(
                doc_id="code-impact",
                doc_type="code_impact",
                graph_node_id="impact:WP0018",
                node_kind="CodeImpactSummary",
                packet_id="WP0018",
                commit_sha="8351639",
                title="WP0018 impact",
                body="Graph service retrieval impact.",
                repo_id="repo:amo",
                metadata={"selected_files": ["src/agent_memory_orchestrator/graph_service.py"]},
            ),
        ],
    )

    assert trace["status"] == "active"
    assert trace["trace"]["repo_id"] == "repo:amo"
    assert trace["trace"]["graph_view_id"] == "v2view:repo:amo:main:active"
    assert trace["trace"]["graph_commit_id"] == "v2gcommit:1"
    assert trace["trace"]["central_versions"] == ["kver:file:graph_service"]
    assert trace["trace"]["packets"] == ["WP0018"]
    assert trace["trace"]["commits"] == ["8351639"]
    assert trace["trace"]["evidence_refs"] == ["E01156"]
    assert trace["trace"]["files"] == ["src/agent_memory_orchestrator/graph_service.py"]
    assert trace["trace"]["code_impacts"] == ["impact:WP0018"]


def test_answer_trace_falls_back_to_retrieval_doc_metadata_when_graph_node_missing() -> None:
    result = {
        "query": "what qwen json hardening was done?",
        "hits": [
            {
                "document": RetrievalDocument(
                    doc_id="retrieval:packet",
                    doc_type="reasoning",
                    graph_node_id="sessionjob:reason:WP0003",
                    node_kind="ReasoningNode",
                    packet_id="WP0003",
                    commit_sha="1a7b05d",
                    title="Decision: Fix Ollama usage for Qwen reasoning",
                    body="Disable Ollama thinking for JSON calls.",
                    repo_id="repo:amo",
                    metadata={"evidence_refs": ["E00156"], "selected_files": ["src/agent_memory_orchestrator/llm/qwen.py"]},
                ).as_dict(),
                "score": 0.9,
            }
        ],
    }

    answer = _answer_from_retrieval_result(result, graph_store=InMemoryGraphStore())

    trace = answer["citations"][0]["trace"]
    assert trace["source"] == "retrieval_document_metadata"
    assert trace["node_count"] == 1
    assert trace["support"]["packet_ids"] == ["WP0003"]
    assert trace["support"]["commit_shas"] == ["1a7b05d"]
    assert trace["support"]["evidence_ids"] == ["E00156"]


def test_central_trace_enrichment_matches_commit_and_file_versions() -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="kver:commit:8351639",
            kind="KnowledgeVersion",
            label="commit version",
            status="active",
            metadata={
                "atom_kind": "commit",
                "repo_id": "repo:amo",
                "graph_commit_id": "v2gcommit:1",
                "version_metadata": {"canonical_key": "commit|repo:amo|8351639abcdef"},
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="kver:file:graph_service",
            kind="KnowledgeVersion",
            label="file version",
            status="active",
            metadata={
                "atom_kind": "file",
                "repo_id": "repo:amo",
                "graph_commit_id": "v2gcommit:previous",
                "version_metadata": {"canonical_key": "file|repo:amo|src/agent_memory_orchestrator/graph_service.py"},
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="kver:file:other",
            kind="KnowledgeVersion",
            label="other file version",
            status="active",
            metadata={
                "atom_kind": "file",
                "repo_id": "repo:amo",
                "graph_commit_id": "v2gcommit:1",
                "version_metadata": {"canonical_key": "file|repo:amo|src/other.py"},
            },
        )
    )

    versions = _active_central_versions_for_support(
        graph,
        repo_id="repo:amo",
        graph_commit_id="v2gcommit:head",
        support_docs=[
            {
                "doc_type": "file_impact",
                "node_kind": "FileImpactSummary",
                "commit_sha": "8351639",
                "metadata": {"path": "src/agent_memory_orchestrator/graph_service.py"},
            }
        ],
    )

    assert {version["id"] for version in versions} == {"kver:commit:8351639", "kver:file:graph_service"}


def test_answer_trace_prefers_visible_query_match_over_metadata_only_match() -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:fix",
            kind="ReasoningNode",
            label="Fix: Hook behavior change to capture-only",
            summary="Hooks became capture-only and retrieval moved to explicit graph search.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639", "node_type": "Fix"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:problem",
            kind="ReasoningNode",
            label="Problem: Hook execution timeout",
            summary="Manual smoke tests passed but real Codex hook execution timed out.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639", "node_type": "Problem"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="reason:bm25:problem",
            kind="ReasoningNode",
            label="Problem: Noisy BM25 retrieval",
            summary="BM25 finds exact terms rather than understanding user intent.",
            metadata={
                "packet_id": "WP0018",
                "commit_sha": "8351639",
                "node_type": "Problem",
                "reason": "This mentions codex hooks only as an example of exact-term matching.",
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="packet:WP0018",
            kind="Packet",
            label="WP0018",
            summary="Hook behavior packet.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    for edge in [
        GraphEdge("e1", "reason:hooks:fix", "packet:WP0018", "REASON_NODE_IN_PACKET"),
        GraphEdge("e2", "reason:hooks:problem", "packet:WP0018", "REASON_NODE_IN_PACKET"),
        GraphEdge("e3", "reason:bm25:problem", "packet:WP0018", "REASON_NODE_IN_PACKET"),
    ]:
        graph.upsert_edge(edge)

    trace = build_answer_trace(
        seed_node_id="reason:hooks:fix",
        graph_store=graph,
        query="what decisions were made about Codex hooks?",
    )

    problems = [item for item in trace["chain"] if item["role"] == "Problem"]
    assert len(problems) == 1
    assert problems[0]["id"] == "reason:hooks:problem"


def test_graph_service_answer_includes_multihop_trace(tmp_path: Path) -> None:
    graph = InMemoryGraphStore()
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:problem",
            kind="ReasoningNode",
            label="Problem: Hook execution timeout",
            summary="Hook execution timed out when retrieval ran inside UserPromptSubmit.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639", "node_type": "Problem"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="reason:hooks:fix",
            kind="ReasoningNode",
            label="Fix: Hook behavior change to capture-only",
            summary="Hooks became capture-only and explicit graph search performs retrieval.",
            metadata={
                "packet_id": "WP0018",
                "commit_sha": "8351639",
                "node_type": "Fix",
                "statement": "Hooks became capture-only and explicit graph search performs retrieval.",
            },
        )
    )
    graph.upsert_node(
        GraphNode(
            id="packet:WP0018",
            kind="Packet",
            label="WP0018",
            summary="Hook behavior packet.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="commit:8351639",
            kind="Commit",
            label="8351639",
            summary="feat(graph): pivot memory runtime to Kuzu GraphRAG",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="hunk:hook",
            kind="CodeHunk",
            label="src/agent_memory_orchestrator/hook.py:40",
            summary="Changed hook response to capture-only.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    graph.upsert_node(
        GraphNode(
            id="code:hook_response",
            kind="CodeNode",
            label="src/agent_memory_orchestrator/hook.py::_hook_response",
            summary="Builds capture-only hook response.",
            metadata={"packet_id": "WP0018", "commit_sha": "8351639"},
        )
    )
    for edge in [
        GraphEdge("e1", "reason:hooks:problem", "packet:WP0018", "REASON_NODE_IN_PACKET"),
        GraphEdge("e2", "reason:hooks:fix", "packet:WP0018", "REASON_NODE_IN_PACKET"),
        GraphEdge("e3", "reason:hooks:fix", "commit:8351639", "REASON_NODE_EXPLAINS_COMMIT"),
        GraphEdge("e4", "commit:8351639", "hunk:hook", "COMMIT_PRODUCED_HUNK"),
        GraphEdge("e5", "hunk:hook", "code:hook_response", "HUNK_MAPS_TO_CODE_NODE"),
    ]:
        graph.upsert_edge(edge)

    svc = GraphRagService(_settings(tmp_path), store=graph, planner=DeterministicPlanner())
    try:
        svc.rebuild_retrieval_index(session_id="")
        result = svc.retrieve_indexed_graph(
            query="what decisions were made about Codex hooks?",
            limit=1,
            use_vector=False,
        )
    finally:
        svc.close()

    assert result["ok"] is True
    assert "Evidence:" in result["answer"]["text"]
    assert "Support: session context, commit-backed, code-linked" in result["answer"]["text"]
    assert "Hooks became capture-only" in result["answer"]["text"]
    citation = result["answer"]["citations"][0]
    assert citation["trace"]["support"]["packet_ids"] == ["WP0018"]
    assert "code:hook_response" in citation["trace"]["support"]["code_node_ids"]


def test_unique_nonempty_dedupes_nested_citation_values() -> None:
    assert _unique_nonempty(["E0001", ["E0001", "E0002"], ("", None, "E0002"), {"E0003"}]) == [
        "E0001",
        "E0002",
        "E0003",
    ]
