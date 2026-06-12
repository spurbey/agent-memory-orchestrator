from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness.projection import DEFAULT_PROJECTION_VERSION
from agent_memory_orchestrator.domain.semantic_harness.projection import HarnessProjectionDocument
from agent_memory_orchestrator.domain.semantic_harness.projection import HarnessProjectionSet
from agent_memory_orchestrator.domain.semantic_harness.projection import build_projection_set
from agent_memory_orchestrator.domain.semantic_harness.projection import projection_set_id
from agent_memory_orchestrator.domain.semantic_harness.snapshots import graph_snapshot_identity
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph

from .schema import ensure_semantic_harness_schema


class SQLiteProjectionCache:
    """Persistent projection cache keyed by graph snapshot and projection version."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path)
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        ensure_semantic_harness_schema(self._conn)
        self._hits = 0
        self._misses = 0

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteProjectionCache:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def get_or_build(
        self,
        graph: StructuralHarnessGraph,
        *,
        projection_version: str = DEFAULT_PROJECTION_VERSION,
    ) -> HarnessProjectionSet:
        snapshot = graph_snapshot_identity(graph)
        projection_id = projection_set_id(snapshot.graph_snapshot_id, projection_version=projection_version)
        if cached := self.get(projection_id):
            self._hits += 1
            return cached
        projection = build_projection_set(graph, projection_version=projection_version)
        self.save(projection)
        self._misses += 1
        return projection

    def get(self, projection_id: str) -> HarnessProjectionSet | None:
        row = self._conn.execute(
            """
            SELECT projection_id, repo_id, projection_version, graph_snapshot_id, graph_schema_version
            FROM semantic_harness_projection_sets
            WHERE projection_id = ?
            """,
            (projection_id,),
        ).fetchone()
        if row is None:
            return None
        documents = self._documents_for_projection(projection_id)
        return HarnessProjectionSet(
            repo_id=str(row["repo_id"]),
            projection_id=str(row["projection_id"]),
            projection_version=str(row["projection_version"]),
            graph_snapshot_id=str(row["graph_snapshot_id"]),
            graph_schema_version=str(row["graph_schema_version"]),
            documents=documents,
        )

    def get_for_graph(
        self,
        graph_snapshot_id: str,
        *,
        projection_version: str = DEFAULT_PROJECTION_VERSION,
    ) -> HarnessProjectionSet | None:
        return self.get(projection_set_id(graph_snapshot_id, projection_version=projection_version))

    def save(self, projection: HarnessProjectionSet) -> None:
        with self._conn:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO semantic_harness_projection_sets (
                    projection_id,
                    repo_id,
                    projection_version,
                    graph_snapshot_id,
                    graph_schema_version,
                    document_ids_hash,
                    document_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    projection.projection_id,
                    projection.repo_id,
                    projection.projection_version,
                    projection.graph_snapshot_id,
                    projection.graph_schema_version,
                    projection.document_ids_hash,
                    projection.document_count,
                ),
            )
            self._conn.execute(
                "DELETE FROM semantic_harness_projection_documents WHERE projection_id = ?",
                (projection.projection_id,),
            )
            self._conn.executemany(
                """
                INSERT INTO semantic_harness_projection_documents (
                    projection_id,
                    doc_id,
                    repo_id,
                    source_node_id,
                    source_kind,
                    doc_type,
                    title,
                    text,
                    metadata_json,
                    content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(_document_row(projection.projection_id, document) for document in projection.documents),
            )

    def stats(self) -> dict[str, int]:
        return {"hits": self._hits, "misses": self._misses}

    def _documents_for_projection(self, projection_id: str) -> tuple[HarnessProjectionDocument, ...]:
        rows = self._conn.execute(
            """
            SELECT doc_id, repo_id, source_node_id, source_kind, doc_type, title, text, metadata_json
            FROM semantic_harness_projection_documents
            WHERE projection_id = ?
            ORDER BY doc_id
            """,
            (projection_id,),
        ).fetchall()
        return tuple(_document_from_row(row) for row in rows)


def _document_row(projection_id: str, document: HarnessProjectionDocument) -> tuple[str, ...]:
    return (
        projection_id,
        document.doc_id,
        document.repo_id,
        document.source_node_id,
        document.source_kind,
        document.doc_type,
        document.title,
        document.text,
        _json_dumps(document.metadata),
        document.content_hash,
    )


def _document_from_row(row: sqlite3.Row) -> HarnessProjectionDocument:
    return HarnessProjectionDocument(
        doc_id=str(row["doc_id"]),
        repo_id=str(row["repo_id"]),
        source_node_id=str(row["source_node_id"]),
        source_kind=str(row["source_kind"]),
        doc_type=str(row["doc_type"]),
        title=str(row["title"]),
        text=str(row["text"]),
        metadata=_json_loads(str(row["metadata_json"])),
    )


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str) -> dict[str, Any]:
    loaded = json.loads(value) if value else {}
    return loaded if isinstance(loaded, dict) else {}


__all__ = ["SQLiteProjectionCache"]
