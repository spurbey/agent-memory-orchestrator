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
        """
    )
    conn.commit()


__all__ = ["SCHEMA_VERSION", "ensure_semantic_harness_schema"]
