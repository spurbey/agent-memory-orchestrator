from __future__ import annotations

import sqlite3

from agent_memory_orchestrator.core.db import init_schema
from agent_memory_orchestrator.infrastructure.sqlite.retrieval_store import RetrievalIndexStore


def test_init_schema_migrates_old_v2_repo_columns_before_indexes() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE v2_session_jobs (
          job_id TEXT PRIMARY KEY,
          session_id TEXT NOT NULL,
          pipeline_version TEXT NOT NULL,
          graph_schema_version TEXT NOT NULL,
          status TEXT NOT NULL,
          current_stage TEXT NOT NULL DEFAULT '',
          artifact_dir TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL
        );
        CREATE TABLE v2_graph_commits (
          graph_commit_id TEXT PRIMARY KEY,
          plan_id TEXT NOT NULL DEFAULT '',
          job_id TEXT NOT NULL DEFAULT '',
          branch TEXT NOT NULL DEFAULT 'main',
          parent_graph_commit_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'planned',
          pipeline_version TEXT NOT NULL DEFAULT 'v2-reset-2026-05',
          graph_schema_version TEXT NOT NULL DEFAULT 'v2',
          algorithm_versions_json TEXT NOT NULL DEFAULT '{}',
          added_nodes_json TEXT NOT NULL DEFAULT '[]',
          added_edges_json TEXT NOT NULL DEFAULT '[]',
          status_updates_json TEXT NOT NULL DEFAULT '[]',
          diagnostics_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE v2_graph_views (
          view_id TEXT PRIMARY KEY,
          branch TEXT NOT NULL DEFAULT 'main',
          mode TEXT NOT NULL DEFAULT 'active',
          graph_commit_id TEXT NOT NULL DEFAULT '',
          status TEXT NOT NULL DEFAULT 'active',
          metadata_json TEXT NOT NULL DEFAULT '{}',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE v2_central_merge_locks (
          branch TEXT PRIMARY KEY,
          lock_owner TEXT NOT NULL DEFAULT '',
          lock_expires_at TEXT NOT NULL DEFAULT '',
          expected_parent_graph_commit_id TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        """
    )

    init_schema(conn)

    for table in ("v2_session_jobs", "v2_graph_commits", "v2_graph_views", "v2_central_merge_locks"):
        assert "repo_id" in _columns(conn, table)
    indexes = _indexes(conn)
    assert "idx_v2_session_jobs_repo" in indexes
    assert "idx_v2_graph_commits_branch" in indexes
    assert "idx_v2_graph_views_lookup" in indexes


def test_retrieval_index_store_migrates_old_retrieval_documents_repo_id() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE retrieval_documents (
          doc_id TEXT PRIMARY KEY,
          doc_type TEXT NOT NULL,
          graph_node_id TEXT NOT NULL,
          node_kind TEXT NOT NULL,
          packet_id TEXT NOT NULL DEFAULT '',
          commit_sha TEXT NOT NULL DEFAULT '',
          title TEXT NOT NULL,
          body TEXT NOT NULL,
          body_char_count INTEGER NOT NULL,
          chunk_index INTEGER NOT NULL DEFAULT 1,
          chunk_count INTEGER NOT NULL DEFAULT 1,
          memory_class TEXT NOT NULL,
          importance REAL NOT NULL DEFAULT 0.5,
          metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )

    RetrievalIndexStore(conn)

    assert "repo_id" in _columns(conn, "retrieval_documents")
    assert "idx_retrieval_documents_repo" in _indexes(conn)


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    return {str(row["name"]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
