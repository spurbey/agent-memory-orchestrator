from __future__ import annotations

import json
import sqlite3
from dataclasses import replace as dataclass_replace
from datetime import datetime, timezone
from typing import Any, Iterable

from ...core.db import init_schema
from ...domain.retrieval.models import RetrievalCandidate
from ...domain.retrieval.models import RetrievalDocument
from ...domain.retrieval.text import exact_tokens as _exact_tokens
from ...domain.retrieval.text import fts_query as _fts_query
from ...domain.retrieval.text import normalize as _normalize
from ...domain.retrieval.text import terms as _terms


class RetrievalIndexStore:
    """SQLite/FTS storage for graph-attached retrieval documents.

    Kuzu remains graph truth. This store is a searchable document/index layer
    where every row points back to a Kuzu graph node id.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._fts_enabled = True
        init_schema(conn)
        self.init_schema()

    def init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_documents (
              doc_id TEXT PRIMARY KEY,
              doc_type TEXT NOT NULL,
              graph_node_id TEXT NOT NULL,
              node_kind TEXT NOT NULL,
              repo_id TEXT NOT NULL DEFAULT '',
              projection_id TEXT NOT NULL DEFAULT '',
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
            )
            """
        )
        self._ensure_retrieval_document_columns()
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_retrieval_documents_node
            ON retrieval_documents(graph_node_id, doc_type)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_retrieval_documents_packet
            ON retrieval_documents(packet_id, commit_sha)
            """
        )
        self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_retrieval_documents_repo
            ON retrieval_documents(repo_id, projection_id, doc_type, node_kind)
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS retrieval_projections (
              projection_id TEXT PRIMARY KEY,
              repo_id TEXT NOT NULL,
              projection_version TEXT NOT NULL,
              source_artifact_hash TEXT NOT NULL DEFAULT '',
              doc_content_hash TEXT NOT NULL DEFAULT '',
              status TEXT NOT NULL DEFAULT 'building',
              metadata_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              activated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_retrieval_projection (
              repo_id TEXT PRIMARY KEY,
              projection_id TEXT NOT NULL,
              updated_at TEXT NOT NULL
            )
            """
        )
        try:
            self._ensure_retrieval_fts_schema()
        except sqlite3.OperationalError:
            self._fts_enabled = False
        self.conn.commit()

    def _ensure_retrieval_document_columns(self) -> None:
        columns = _table_columns(self.conn, "retrieval_documents")
        migrations = {
            "repo_id": "repo_id TEXT NOT NULL DEFAULT ''",
            "projection_id": "projection_id TEXT NOT NULL DEFAULT ''",
            "packet_id": "packet_id TEXT NOT NULL DEFAULT ''",
            "commit_sha": "commit_sha TEXT NOT NULL DEFAULT ''",
            "body_char_count": "body_char_count INTEGER NOT NULL DEFAULT 0",
            "chunk_index": "chunk_index INTEGER NOT NULL DEFAULT 1",
            "chunk_count": "chunk_count INTEGER NOT NULL DEFAULT 1",
            "memory_class": "memory_class TEXT NOT NULL DEFAULT 'graph_context'",
            "importance": "importance REAL NOT NULL DEFAULT 0.5",
            "metadata_json": "metadata_json TEXT NOT NULL DEFAULT '{}'",
        }
        for column, ddl in migrations.items():
            if column not in columns:
                self.conn.execute(f"ALTER TABLE retrieval_documents ADD COLUMN {ddl}")

    def _ensure_retrieval_fts_schema(self) -> None:
        expected = ("doc_id", "title", "body", "packet_id", "commit_sha", "node_kind", "memory_class")
        existing = _table_columns(self.conn, "retrieval_documents_fts")
        recreated = False
        if existing and tuple(existing) != expected:
            self.conn.execute("DROP TABLE retrieval_documents_fts")
            recreated = True
        self.conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_documents_fts USING fts5(
              doc_id UNINDEXED,
              title,
              body,
              packet_id,
              commit_sha,
              node_kind,
              memory_class
            )
            """
        )
        if recreated:
            self._rebuild_fts_from_documents()

    def _rebuild_fts_from_documents(self) -> None:
        rows = self.conn.execute(
            """
            SELECT doc_id, title, body, packet_id, commit_sha, node_kind, memory_class
            FROM retrieval_documents
            """
        ).fetchall()
        for row in rows:
            self.conn.execute(
                """
                INSERT INTO retrieval_documents_fts(
                  doc_id, title, body, packet_id, commit_sha, node_kind, memory_class
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["doc_id"],
                    row["title"],
                    row["body"],
                    row["packet_id"],
                    row["commit_sha"],
                    row["node_kind"],
                    row["memory_class"],
                ),
            )

    def upsert_documents(self, docs: Iterable[RetrievalDocument]) -> int:
        count = 0
        for doc in docs:
            self.conn.execute(
                """
                INSERT INTO retrieval_documents(
                  doc_id, doc_type, graph_node_id, node_kind, packet_id, commit_sha,
                  repo_id, projection_id, title, body, body_char_count, chunk_index, chunk_count,
                  memory_class, importance, metadata_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                  doc_type=excluded.doc_type,
                  graph_node_id=excluded.graph_node_id,
                  node_kind=excluded.node_kind,
                  repo_id=excluded.repo_id,
                  projection_id=excluded.projection_id,
                  packet_id=excluded.packet_id,
                  commit_sha=excluded.commit_sha,
                  title=excluded.title,
                  body=excluded.body,
                  body_char_count=excluded.body_char_count,
                  chunk_index=excluded.chunk_index,
                  chunk_count=excluded.chunk_count,
                  memory_class=excluded.memory_class,
                  importance=excluded.importance,
                  metadata_json=excluded.metadata_json
                """,
                (
                    doc.doc_id,
                    doc.doc_type,
                    doc.graph_node_id,
                    doc.node_kind,
                    doc.packet_id,
                    doc.commit_sha,
                    doc.repo_id,
                    doc.projection_id,
                    doc.title,
                    doc.body,
                    doc.body_char_count,
                    doc.chunk_index,
                    doc.chunk_count,
                    doc.memory_class,
                    doc.importance,
                    json.dumps(doc.metadata, sort_keys=True),
                ),
            )
            if self._fts_enabled:
                self.conn.execute("DELETE FROM retrieval_documents_fts WHERE doc_id = ?", (doc.doc_id,))
                self.conn.execute(
                    """
                    INSERT INTO retrieval_documents_fts(
                      doc_id, title, body, packet_id, commit_sha, node_kind, memory_class
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        doc.doc_id,
                        doc.title,
                        doc.body,
                        doc.packet_id,
                        doc.commit_sha,
                        doc.node_kind,
                        doc.memory_class,
                    ),
                )
            count += 1
        self.conn.commit()
        return count

    def replace_documents(self, docs: Iterable[RetrievalDocument], *, repo_id: str = "") -> int:
        safe_repo_id = str(repo_id or "").strip()
        if safe_repo_id:
            rows = self.conn.execute("SELECT doc_id FROM retrieval_documents WHERE repo_id = ?", (safe_repo_id,)).fetchall()
            doc_ids = [str(row["doc_id"]) for row in rows]
            self.conn.execute("DELETE FROM retrieval_documents WHERE repo_id = ?", (safe_repo_id,))
            if self._fts_enabled and doc_ids:
                placeholders = ",".join("?" for _ in doc_ids)
                self.conn.execute(f"DELETE FROM retrieval_documents_fts WHERE doc_id IN ({placeholders})", doc_ids)
        else:
            self.conn.execute("DELETE FROM retrieval_documents")
            if self._fts_enabled:
                self.conn.execute("DELETE FROM retrieval_documents_fts")
        self.conn.commit()
        return self.upsert_documents(docs)

    def upsert_projection(
        self,
        *,
        projection_id: str,
        repo_id: str,
        projection_version: str,
        source_artifact_hash: str,
        doc_content_hash: str,
        status: str = "building",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = _utc_now()
        self.conn.execute(
            """
            INSERT INTO retrieval_projections(
              projection_id, repo_id, projection_version, source_artifact_hash,
              doc_content_hash, status, metadata_json, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(projection_id) DO UPDATE SET
              repo_id=excluded.repo_id,
              projection_version=excluded.projection_version,
              source_artifact_hash=excluded.source_artifact_hash,
              doc_content_hash=excluded.doc_content_hash,
              status=excluded.status,
              metadata_json=excluded.metadata_json
            """,
            (
                projection_id,
                str(repo_id or "").strip(),
                projection_version,
                source_artifact_hash,
                doc_content_hash,
                status,
                json.dumps(metadata or {}, sort_keys=True),
                now,
            ),
        )
        self.conn.commit()
        return self.projection(projection_id) or {}

    def projection(self, projection_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM retrieval_projections WHERE projection_id = ?", (projection_id,)).fetchone()
        return _projection_row(row) if row is not None else None

    def active_projection(self, repo_id: str) -> dict[str, Any] | None:
        safe_repo_id = str(repo_id or "").strip()
        if not safe_repo_id:
            return None
        row = self.conn.execute(
            """
            SELECT retrieval_projections.*
            FROM active_retrieval_projection
            JOIN retrieval_projections ON retrieval_projections.projection_id = active_retrieval_projection.projection_id
            WHERE active_retrieval_projection.repo_id = ?
            """,
            (safe_repo_id,),
        ).fetchone()
        return _projection_row(row) if row is not None else None

    def active_projection_id(self, repo_id: str) -> str:
        projection = self.active_projection(repo_id)
        return str(projection.get("projection_id") or "") if projection else ""

    def set_projection_status(self, projection_id: str, status: str) -> None:
        self.conn.execute("UPDATE retrieval_projections SET status=? WHERE projection_id=?", (status, projection_id))
        self.conn.commit()

    def activate_projection(self, *, repo_id: str, projection_id: str) -> dict[str, Any]:
        now = _utc_now()
        safe_repo_id = str(repo_id or "").strip()
        self.conn.execute(
            "UPDATE retrieval_projections SET status='historical' WHERE repo_id=? AND projection_id != ? AND status='active'",
            (safe_repo_id, projection_id),
        )
        self.conn.execute("UPDATE retrieval_projections SET status='active', activated_at=? WHERE projection_id=?", (now, projection_id))
        self.conn.execute(
            """
            INSERT INTO active_retrieval_projection(repo_id, projection_id, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(repo_id) DO UPDATE SET
              projection_id=excluded.projection_id,
              updated_at=excluded.updated_at
            """,
            (safe_repo_id, projection_id, now),
        )
        self.conn.commit()
        return self.active_projection(safe_repo_id) or {}

    def replace_projection_documents(self, docs: Iterable[RetrievalDocument], *, repo_id: str, projection_id: str) -> int:
        safe_repo_id = str(repo_id or "").strip()
        safe_projection_id = str(projection_id or "").strip()
        rows = self.conn.execute("SELECT doc_id FROM retrieval_documents WHERE projection_id = ?", (safe_projection_id,)).fetchall()
        doc_ids = [str(row["doc_id"]) for row in rows]
        self.conn.execute("DELETE FROM retrieval_documents WHERE projection_id = ?", (safe_projection_id,))
        if self._fts_enabled and doc_ids:
            placeholders = ",".join("?" for _ in doc_ids)
            self.conn.execute(f"DELETE FROM retrieval_documents_fts WHERE doc_id IN ({placeholders})", doc_ids)
        self.conn.commit()
        projected_docs = [
            dataclass_replace(
                doc,
                repo_id=safe_repo_id,
                projection_id=safe_projection_id,
                metadata={**doc.metadata, "projection_id": safe_projection_id},
            )
            for doc in docs
        ]
        return self.upsert_documents(projected_docs)

    def list_repo_documents_all(self, *, repo_id: str, limit: int = 100000) -> list[RetrievalDocument]:
        safe_repo_id = str(repo_id or "").strip()
        if not safe_repo_id:
            return []
        rows = self.conn.execute(
            """
            SELECT *
            FROM retrieval_documents
            WHERE repo_id = ?
            ORDER BY projection_id, doc_type, graph_node_id, chunk_index
            LIMIT ?
            """,
            (safe_repo_id, int(limit)),
        ).fetchall()
        return [_doc_from_row(row) for row in rows]

    def list_documents(self, *, limit: int = 10000, repo_id: str = "") -> list[RetrievalDocument]:
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id:
            if projection_id:
                rows = self.conn.execute(
                    """
                    SELECT * FROM retrieval_documents
                    WHERE repo_id = ? AND projection_id = ?
                    ORDER BY doc_type, graph_node_id, chunk_index
                    LIMIT ?
                    """,
                    (safe_repo_id, projection_id, int(limit)),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT * FROM retrieval_documents
                    WHERE repo_id = ?
                    ORDER BY doc_type, graph_node_id, chunk_index
                    LIMIT ?
                    """,
                    (safe_repo_id, int(limit)),
                ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM retrieval_documents
                ORDER BY doc_type, graph_node_id, chunk_index
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [_doc_from_row(row) for row in rows]

    def get_documents_by_ids(self, doc_ids: Iterable[str], *, repo_id: str = "") -> dict[str, RetrievalDocument]:
        ids = list(dict.fromkeys(doc_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id:
            if projection_id:
                rows = self.conn.execute(
                    f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders}) AND repo_id = ? AND projection_id = ?",
                    [*ids, safe_repo_id, projection_id],
                ).fetchall()
            else:
                rows = self.conn.execute(
                    f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders}) AND repo_id = ?",
                    [*ids, safe_repo_id],
                ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders})",
                ids,
            ).fetchall()
        return {str(row["doc_id"]): _doc_from_row(row) for row in rows}

    def documents_by_graph_node_ids(self, node_ids: Iterable[str], *, repo_id: str = "") -> dict[str, list[RetrievalDocument]]:
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id:
            if projection_id:
                rows = self.conn.execute(
                    f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders}) AND repo_id = ? AND projection_id = ?",
                    [*ids, safe_repo_id, projection_id],
                ).fetchall()
            else:
                rows = self.conn.execute(
                    f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders}) AND repo_id = ?",
                    [*ids, safe_repo_id],
                ).fetchall()
        else:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders})",
                ids,
            ).fetchall()
        out: dict[str, list[RetrievalDocument]] = {}
        for row in rows:
            doc = _doc_from_row(row)
            out.setdefault(doc.graph_node_id, []).append(doc)
        return out

    def bm25_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        if not self._fts_enabled:
            return self.like_search(query, limit=limit, repo_id=repo_id)
        try:
            safe_repo_id = str(repo_id or "").strip()
            projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
            if safe_repo_id:
                if projection_id:
                    rows = self.conn.execute(
                        """
                        SELECT retrieval_documents_fts.doc_id, bm25(retrieval_documents_fts) AS score
                        FROM retrieval_documents_fts
                        JOIN retrieval_documents ON retrieval_documents.doc_id = retrieval_documents_fts.doc_id
                        WHERE retrieval_documents_fts MATCH ? AND retrieval_documents.repo_id = ? AND retrieval_documents.projection_id = ?
                        ORDER BY score ASC
                        LIMIT ?
                        """,
                        (fts_query, safe_repo_id, projection_id, int(limit)),
                    ).fetchall()
                else:
                    rows = self.conn.execute(
                        """
                        SELECT retrieval_documents_fts.doc_id, bm25(retrieval_documents_fts) AS score
                        FROM retrieval_documents_fts
                        JOIN retrieval_documents ON retrieval_documents.doc_id = retrieval_documents_fts.doc_id
                        WHERE retrieval_documents_fts MATCH ? AND retrieval_documents.repo_id = ?
                        ORDER BY score ASC
                        LIMIT ?
                        """,
                        (fts_query, safe_repo_id, int(limit)),
                    ).fetchall()
            else:
                rows = self.conn.execute(
                    """
                    SELECT doc_id, bm25(retrieval_documents_fts) AS score
                    FROM retrieval_documents_fts
                    WHERE retrieval_documents_fts MATCH ?
                    ORDER BY score ASC
                    LIMIT ?
                    """,
                    (fts_query, int(limit)),
                ).fetchall()
        except sqlite3.OperationalError:
            return self.like_search(query, limit=limit, repo_id=repo_id)
        candidates: list[RetrievalCandidate] = []
        for rank, row in enumerate(rows, start=1):
            # SQLite bm25 is lower-is-better and often negative.
            candidates.append(
                RetrievalCandidate(
                    doc_id=str(row["doc_id"]),
                    source="bm25",
                    rank=rank,
                    raw_score=1.0 / (1.0 + abs(float(row["score"]))),
                )
            )
        return candidates

    def like_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        terms = sorted(_terms(query))[:8]
        if not terms:
            return []
        rows = self.list_documents(limit=10000, repo_id=repo_id)
        scored: list[tuple[float, RetrievalDocument]] = []
        for doc in rows:
            text = _normalize(f"{doc.title} {doc.body} {doc.packet_id} {doc.commit_sha} {doc.node_kind}")
            score = sum(1.0 for term in terms if term in text)
            if score:
                scored.append((score, doc))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievalCandidate(doc.doc_id, "bm25_like", rank, score)
            for rank, (score, doc) in enumerate(scored[:limit], start=1)
        ]

    def exact_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        tokens = _exact_tokens(query)
        if not tokens:
            return []
        candidates: list[tuple[float, RetrievalDocument]] = []
        for doc in self.list_documents(limit=10000, repo_id=repo_id):
            haystack = f"{doc.doc_id} {doc.graph_node_id} {doc.title} {doc.body} {json.dumps(doc.metadata, sort_keys=True)}".lower()
            score = 0.0
            for token in tokens:
                if token.lower() in haystack:
                    score += 2.0 if ("/" in token or "::" in token or "." in token) else 1.0
            if score:
                candidates.append((score, doc))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [
            RetrievalCandidate(doc.doc_id, "exact", rank, score)
            for rank, (score, doc) in enumerate(candidates[:limit], start=1)
        ]



def _doc_from_row(row: sqlite3.Row) -> RetrievalDocument:
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    return RetrievalDocument(
        doc_id=str(row["doc_id"]),
        doc_type=str(row["doc_type"]),
        graph_node_id=str(row["graph_node_id"]),
        node_kind=str(row["node_kind"]),
        packet_id=str(row["packet_id"]),
        commit_sha=str(row["commit_sha"]),
        title=str(row["title"]),
        body=str(row["body"]),
        repo_id=str(row["repo_id"]),
        projection_id=str(row["projection_id"]) if "projection_id" in row.keys() else "",
        chunk_index=int(row["chunk_index"]),
        chunk_count=int(row["chunk_count"]),
        memory_class=str(row["memory_class"]),
        importance=float(row["importance"]),
        metadata=metadata,
    )


def _projection_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        metadata = json.loads(str(row["metadata_json"] or "{}"))
    except json.JSONDecodeError:
        metadata = {}
    return {
        "projection_id": str(row["projection_id"]),
        "repo_id": str(row["repo_id"]),
        "projection_version": str(row["projection_version"]),
        "source_artifact_hash": str(row["source_artifact_hash"]),
        "doc_content_hash": str(row["doc_content_hash"]),
        "status": str(row["status"]),
        "created_at": str(row["created_at"]),
        "activated_at": str(row["activated_at"]),
        "metadata": metadata,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(str(row["name"]) for row in rows)



__all__ = ["RetrievalIndexStore"]
