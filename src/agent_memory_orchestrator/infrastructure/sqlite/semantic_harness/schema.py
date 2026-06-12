from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 1


def ensure_semantic_harness_schema(conn: sqlite3.Connection) -> None:
    """Create the SQLite tables used by semantic harness infrastructure adapters."""

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS semantic_harness_nodes (
            repo_id TEXT NOT NULL,
            node_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            label TEXT NOT NULL,
            status TEXT NOT NULL,
            summary TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            PRIMARY KEY (repo_id, node_id)
        );

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_nodes_repo_kind
            ON semantic_harness_nodes (repo_id, kind);

        CREATE TABLE IF NOT EXISTS semantic_harness_edges (
            repo_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            weight REAL NOT NULL,
            confidence REAL NOT NULL,
            metadata_json TEXT NOT NULL,
            PRIMARY KEY (repo_id, source_id, target_id, kind)
        );

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_edges_repo_source_kind
            ON semantic_harness_edges (repo_id, source_id, kind);

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_edges_repo_target_kind
            ON semantic_harness_edges (repo_id, target_id, kind);

        CREATE TABLE IF NOT EXISTS semantic_harness_projection_sets (
            projection_id TEXT PRIMARY KEY,
            repo_id TEXT NOT NULL,
            projection_version TEXT NOT NULL,
            graph_snapshot_id TEXT NOT NULL,
            graph_schema_version TEXT NOT NULL,
            document_ids_hash TEXT NOT NULL,
            document_count INTEGER NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_projection_sets_graph
            ON semantic_harness_projection_sets (graph_snapshot_id, projection_version);

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_projection_sets_repo
            ON semantic_harness_projection_sets (repo_id, projection_version);

        CREATE TABLE IF NOT EXISTS semantic_harness_projection_documents (
            projection_id TEXT NOT NULL,
            doc_id TEXT NOT NULL,
            repo_id TEXT NOT NULL,
            source_node_id TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            title TEXT NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            PRIMARY KEY (projection_id, doc_id)
        );

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_projection_docs_repo_type
            ON semantic_harness_projection_documents (repo_id, doc_type);

        CREATE INDEX IF NOT EXISTS idx_semantic_harness_projection_docs_source
            ON semantic_harness_projection_documents (projection_id, source_node_id);
        """
    )
    conn.commit()


__all__ = ["SCHEMA_VERSION", "ensure_semantic_harness_schema"]
