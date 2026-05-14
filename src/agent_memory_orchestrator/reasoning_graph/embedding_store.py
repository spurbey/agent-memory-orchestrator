from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ..core.db import init_schema


@dataclass(slots=True, frozen=True)
class GraphEmbeddingRecord:
    embedding_id: str
    node_id: str
    node_kind: str
    memory_class: str
    graph_scope: str
    graph_path: str
    session_id: str
    extraction_run_id: str
    embedding_kind: str
    model: str
    dims: int
    content_hash: str
    vector: list[float]
    importance: float = 0.5
    memory_tier: str = "hot"
    status: str = "active"
    created_at: str = ""
    last_accessed_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        node_id: str,
        node_kind: str,
        memory_class: str,
        graph_scope: str,
        graph_path: str,
        session_id: str,
        extraction_run_id: str,
        embedding_kind: str,
        model: str,
        text: str,
        vector: list[float],
        importance: float = 0.5,
        memory_tier: str = "hot",
        status: str = "active",
    ) -> "GraphEmbeddingRecord":
        content_hash = hash_content(text)
        embedding_id = make_embedding_id(
            node_id=node_id,
            embedding_kind=embedding_kind,
            model=model,
            content_hash=content_hash,
        )
        return cls(
            embedding_id=embedding_id,
            node_id=node_id,
            node_kind=node_kind,
            memory_class=memory_class,
            graph_scope=graph_scope,
            graph_path=graph_path,
            session_id=session_id,
            extraction_run_id=extraction_run_id,
            embedding_kind=embedding_kind,
            model=model,
            dims=len(vector),
            content_hash=content_hash,
            vector=[float(value) for value in vector],
            importance=importance,
            memory_tier=memory_tier,
            status=status,
            created_at=utc_now(),
        )

    def as_dict(self, *, include_vector: bool = True) -> dict[str, Any]:
        out = {
            "embedding_id": self.embedding_id,
            "node_id": self.node_id,
            "node_kind": self.node_kind,
            "memory_class": self.memory_class,
            "graph_scope": self.graph_scope,
            "graph_path": self.graph_path,
            "session_id": self.session_id,
            "extraction_run_id": self.extraction_run_id,
            "embedding_kind": self.embedding_kind,
            "model": self.model,
            "dims": self.dims,
            "content_hash": self.content_hash,
            "importance": self.importance,
            "memory_tier": self.memory_tier,
            "status": self.status,
            "created_at": self.created_at,
            "last_accessed_at": self.last_accessed_at,
        }
        if include_vector:
            out["vector"] = self.vector
        return out


@dataclass(slots=True, frozen=True)
class GraphEmbeddingHit:
    embedding_id: str
    node_id: str
    node_kind: str
    memory_class: str
    graph_scope: str
    graph_path: str
    embedding_kind: str
    model: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "embedding_id": self.embedding_id,
            "node_id": self.node_id,
            "node_kind": self.node_kind,
            "memory_class": self.memory_class,
            "graph_scope": self.graph_scope,
            "graph_path": self.graph_path,
            "embedding_kind": self.embedding_kind,
            "model": self.model,
            "score": self.score,
        }


@dataclass(slots=True, frozen=True)
class GraphFaissBuildResult:
    backend: str
    status: str
    item_count: int
    dims: int
    model: str
    embedding_kind: str
    index_path: str = ""
    metadata_path: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "status": self.status,
            "item_count": self.item_count,
            "dims": self.dims,
            "model": self.model,
            "embedding_kind": self.embedding_kind,
            "index_path": self.index_path,
            "metadata_path": self.metadata_path,
            "reason": self.reason,
        }


class GraphEmbeddingStore:
    def __init__(self, conn: sqlite3.Connection, *, db_path: Path) -> None:
        self.conn = conn
        self.db_path = db_path
        init_schema(conn)

    def upsert(self, record: GraphEmbeddingRecord) -> None:
        self.conn.execute(
            """
            INSERT INTO graph_embeddings(
              embedding_id, node_id, node_kind, memory_class, graph_scope, graph_path,
              session_id, extraction_run_id, embedding_kind, model, dims, content_hash,
              vector_json, importance, memory_tier, status, created_at, last_accessed_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(embedding_id) DO UPDATE SET
              node_id=excluded.node_id,
              node_kind=excluded.node_kind,
              memory_class=excluded.memory_class,
              graph_scope=excluded.graph_scope,
              graph_path=excluded.graph_path,
              session_id=excluded.session_id,
              extraction_run_id=excluded.extraction_run_id,
              embedding_kind=excluded.embedding_kind,
              model=excluded.model,
              dims=excluded.dims,
              content_hash=excluded.content_hash,
              vector_json=excluded.vector_json,
              importance=excluded.importance,
              memory_tier=excluded.memory_tier,
              status=excluded.status,
              created_at=excluded.created_at,
              last_accessed_at=excluded.last_accessed_at
            """,
            (
                record.embedding_id,
                record.node_id,
                record.node_kind,
                record.memory_class,
                record.graph_scope,
                record.graph_path,
                record.session_id,
                record.extraction_run_id,
                record.embedding_kind,
                record.model,
                record.dims,
                record.content_hash,
                json.dumps(record.vector, separators=(",", ":")),
                record.importance,
                record.memory_tier,
                record.status,
                record.created_at or utc_now(),
                record.last_accessed_at,
            ),
        )
        self.conn.commit()

    def upsert_many(self, records: Iterable[GraphEmbeddingRecord]) -> int:
        count = 0
        for record in records:
            self.upsert(record)
            count += 1
        return count

    def list_records(
        self,
        *,
        embedding_kind: str = "",
        model: str = "",
        graph_scope: str = "",
        status: str = "active",
        limit: int = 10000,
    ) -> list[GraphEmbeddingRecord]:
        where: list[str] = []
        params: list[Any] = []
        if embedding_kind:
            where.append("embedding_kind = ?")
            params.append(embedding_kind)
        if model:
            where.append("model = ?")
            params.append(model)
        if graph_scope:
            where.append("graph_scope = ?")
            params.append(graph_scope)
        if status:
            where.append("status = ?")
            params.append(status)
        sql = "SELECT * FROM graph_embeddings"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at ASC LIMIT ?"
        params.append(limit)
        return [_record_from_row(row) for row in self.conn.execute(sql, params).fetchall()]

    def search_sqlite(
        self,
        query_vector: list[float],
        *,
        embedding_kind: str,
        model: str,
        graph_scope: str = "",
        limit: int = 10,
    ) -> list[GraphEmbeddingHit]:
        records = self.list_records(embedding_kind=embedding_kind, model=model, graph_scope=graph_scope, status="active")
        hits: list[GraphEmbeddingHit] = []
        for record in records:
            if len(record.vector) != len(query_vector):
                continue
            score = cosine_similarity(query_vector, record.vector)
            if score <= 0:
                continue
            hits.append(_hit_from_record(record, score))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        self._touch([hit.embedding_id for hit in hits[:limit]])
        return hits[:limit]

    def build_faiss_cache(
        self,
        *,
        embedding_kind: str,
        model: str,
        graph_scope: str = "",
    ) -> GraphFaissBuildResult:
        records = self.list_records(embedding_kind=embedding_kind, model=model, graph_scope=graph_scope, status="active")
        if not records:
            return GraphFaissBuildResult("faiss", "skipped", 0, 0, model, embedding_kind, reason="no_vectors")
        dims = records[0].dims
        compatible = [record for record in records if record.dims == dims and record.vector]
        try:
            import faiss  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            return GraphFaissBuildResult(
                "faiss",
                "skipped",
                len(compatible),
                dims,
                model,
                embedding_kind,
                reason=f"faiss_unavailable:{exc}",
            )
        index_path, metadata_path = self._faiss_paths(embedding_kind=embedding_kind, model=model, graph_scope=graph_scope)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        matrix = np.array([record.vector for record in compatible], dtype="float32")
        faiss.normalize_L2(matrix)
        index = faiss.IndexFlatIP(dims)
        index.add(matrix)
        faiss.write_index(index, str(index_path))
        metadata_path.write_text(
            json.dumps(
                {
                    "embedding_ids": [record.embedding_id for record in compatible],
                    "node_ids": [record.node_id for record in compatible],
                    "dims": dims,
                    "model": model,
                    "embedding_kind": embedding_kind,
                    "graph_scope": graph_scope,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return GraphFaissBuildResult(
            "faiss",
            "completed",
            len(compatible),
            dims,
            model,
            embedding_kind,
            str(index_path),
            str(metadata_path),
        )

    def search_faiss(
        self,
        query_vector: list[float],
        *,
        embedding_kind: str,
        model: str,
        graph_scope: str = "",
        limit: int = 10,
    ) -> tuple[list[GraphEmbeddingHit], str]:
        index_path, metadata_path = self._faiss_paths(embedding_kind=embedding_kind, model=model, graph_scope=graph_scope)
        if not index_path.exists() or not metadata_path.exists():
            return [], "index_missing"
        try:
            import faiss  # type: ignore
            import numpy as np  # type: ignore
        except Exception as exc:  # pragma: no cover - environment dependent
            return [], f"faiss_unavailable:{exc}"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        embedding_ids = [str(value) for value in metadata.get("embedding_ids") or []]
        dims = int(metadata.get("dims") or 0)
        if not query_vector or len(query_vector) != dims:
            return [], "dimension_mismatch"
        index = faiss.read_index(str(index_path))
        query = np.array([query_vector], dtype="float32")
        faiss.normalize_L2(query)
        scores, indices = index.search(query, max(1, limit))
        ids = [embedding_ids[idx] for idx in indices[0].tolist() if 0 <= idx < len(embedding_ids)]
        if not ids:
            return [], "no_candidates"
        rows = self._records_by_embedding_ids(ids)
        by_id = {record.embedding_id: record for record in rows}
        hits: list[GraphEmbeddingHit] = []
        for idx, score in zip(indices[0].tolist(), scores[0].tolist()):
            if idx < 0 or idx >= len(embedding_ids):
                continue
            record = by_id.get(embedding_ids[idx])
            if record is None:
                continue
            hits.append(_hit_from_record(record, float(score)))
        self._touch([hit.embedding_id for hit in hits])
        return hits, "completed"

    def search(
        self,
        query_vector: list[float],
        *,
        embedding_kind: str,
        model: str,
        graph_scope: str = "",
        limit: int = 10,
        backend: str = "auto",
    ) -> tuple[list[GraphEmbeddingHit], str]:
        if backend in {"auto", "faiss"}:
            hits, status = self.search_faiss(
                query_vector,
                embedding_kind=embedding_kind,
                model=model,
                graph_scope=graph_scope,
                limit=limit,
            )
            if hits or backend == "faiss":
                return hits, f"faiss:{status}"
        return (
            self.search_sqlite(
                query_vector,
                embedding_kind=embedding_kind,
                model=model,
                graph_scope=graph_scope,
                limit=limit,
            ),
            "sqlite:completed",
        )

    def _records_by_embedding_ids(self, embedding_ids: list[str]) -> list[GraphEmbeddingRecord]:
        placeholders = ",".join("?" for _ in embedding_ids)
        rows = self.conn.execute(
            f"SELECT * FROM graph_embeddings WHERE embedding_id IN ({placeholders})",
            embedding_ids,
        ).fetchall()
        return [_record_from_row(row) for row in rows]

    def _touch(self, embedding_ids: list[str]) -> None:
        if not embedding_ids:
            return
        placeholders = ",".join("?" for _ in embedding_ids)
        self.conn.execute(
            f"UPDATE graph_embeddings SET last_accessed_at = ? WHERE embedding_id IN ({placeholders})",
            [utc_now(), *embedding_ids],
        )
        self.conn.commit()

    def _faiss_paths(self, *, embedding_kind: str, model: str, graph_scope: str) -> tuple[Path, Path]:
        safe = _safe_part(f"graph_{graph_scope or 'all'}_{embedding_kind}_{model}")
        index_dir = self.db_path.parent / "indexes" / self.db_path.stem
        return index_dir / f"{safe}.faiss", index_dir / f"{safe}.json"


def hash_content(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def make_embedding_id(*, node_id: str, embedding_kind: str, model: str, content_hash: str) -> str:
    digest = hashlib.sha256(f"{node_id}|{embedding_kind}|{model}|{content_hash}".encode("utf-8")).hexdigest()[:24]
    return f"emb:{embedding_kind}:{digest}"


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_from_row(row: sqlite3.Row) -> GraphEmbeddingRecord:
    return GraphEmbeddingRecord(
        embedding_id=str(row["embedding_id"]),
        node_id=str(row["node_id"]),
        node_kind=str(row["node_kind"]),
        memory_class=str(row["memory_class"]),
        graph_scope=str(row["graph_scope"]),
        graph_path=str(row["graph_path"]),
        session_id=str(row["session_id"]),
        extraction_run_id=str(row["extraction_run_id"]),
        embedding_kind=str(row["embedding_kind"]),
        model=str(row["model"]),
        dims=int(row["dims"]),
        content_hash=str(row["content_hash"]),
        vector=[float(value) for value in json.loads(row["vector_json"])],
        importance=float(row["importance"]),
        memory_tier=str(row["memory_tier"]),
        status=str(row["status"]),
        created_at=str(row["created_at"]),
        last_accessed_at=str(row["last_accessed_at"]),
    )


def _hit_from_record(record: GraphEmbeddingRecord, score: float) -> GraphEmbeddingHit:
    return GraphEmbeddingHit(
        embedding_id=record.embedding_id,
        node_id=record.node_id,
        node_kind=record.node_kind,
        memory_class=record.memory_class,
        graph_scope=record.graph_scope,
        graph_path=record.graph_path,
        embedding_kind=record.embedding_kind,
        model=record.model,
        score=round(float(score), 6),
    )


def _safe_part(value: str) -> str:
    out = []
    for ch in value.lower():
        if ch.isalnum():
            out.append(ch)
        else:
            out.append("_")
    return "_".join(part for part in "".join(out).split("_") if part)
