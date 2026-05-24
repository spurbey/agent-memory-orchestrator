from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Iterable, Protocol

from ..core.db import init_schema
from ..graph.store import GraphStore
from ..llm.rerankers import RerankCandidate
from ..llm.rerankers import rerank_candidates
from .embedding_store import GraphEmbeddingHit
from .embedding_store import GraphEmbeddingRecord
from .embedding_store import GraphEmbeddingStore
from .embedding_store import hash_content


RETRIEVAL_EMBEDDING_KIND = "retrieval_text"
SESSION_RETRIEVAL_NODE_KINDS = (
    "ReasoningNode",
    "DecisionUnit",
    "DecisionThread",
    "WorkChange",
    "GitCommit",
    "Commit",
    "CodeNode",
    "Symbol",
    "SymbolVersion",
    "EvidenceRef",
    "Evidence",
)
CENTRAL_RETRIEVAL_NODE_KINDS = (
    "KnowledgeVersion",
    "KnowledgeAtom",
    "GraphCommit",
    "GraphView",
)
DEFAULT_RETRIEVAL_NODE_KINDS = SESSION_RETRIEVAL_NODE_KINDS + CENTRAL_RETRIEVAL_NODE_KINDS

QUERY_STOPWORDS = {
    "about",
    "after",
    "again",
    "and",
    "are",
    "code",
    "did",
    "does",
    "for",
    "from",
    "how",
    "into",
    "made",
    "make",
    "the",
    "this",
    "was",
    "what",
    "when",
    "where",
    "which",
    "why",
    "were",
    "with",
}

VERSION_FLOW_OPERATOR_TERMS = {
    "flow",
    "history",
    "show",
    "symbol",
    "version",
    "versions",
}

DECISION_HISTORY_OPERATOR_TERMS = {
    "decision",
    "decide",
    "decided",
    "made",
}

CODE_WHY_OPERATOR_TERMS = {
    "change",
    "changed",
    "code",
    "file",
}

AGENT_CONTEXT_TERMS = {
    "agent",
    "claude",
    "codex",
}

HOOK_QUERY_EXPANSION_TERMS = {
    "capture",
    "inject",
    "injection",
    "prompt",
    "userpromptsubmit",
}


class TextEmbeddingProvider(Protocol):
    def embed(self, text: str) -> list[float]:
        ...


@dataclass(slots=True, frozen=True)
class RetrievalDocument:
    doc_id: str
    doc_type: str
    graph_node_id: str
    node_kind: str
    packet_id: str
    commit_sha: str
    title: str
    body: str
    chunk_index: int = 1
    chunk_count: int = 1
    memory_class: str = "graph_context"
    importance: float = 0.5
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def body_char_count(self) -> int:
        return len(self.body)

    def embedding_text(self) -> str:
        return "\n".join(part for part in (self.title.strip(), self.body.strip()) if part)

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "graph_node_id": self.graph_node_id,
            "node_kind": self.node_kind,
            "packet_id": self.packet_id,
            "commit_sha": self.commit_sha,
            "title": self.title,
            "body": self.body,
            "body_char_count": self.body_char_count,
            "chunk_index": self.chunk_index,
            "chunk_count": self.chunk_count,
            "memory_class": self.memory_class,
            "importance": self.importance,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class RetrievalCandidate:
    doc_id: str
    source: str
    rank: int
    raw_score: float


@dataclass(slots=True, frozen=True)
class RetrievalHit:
    document: RetrievalDocument
    score: float
    sources: tuple[str, ...]
    reasons: tuple[str, ...]
    graph_node: dict[str, Any]
    neighbors: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "document": self.document.as_dict(),
            "score": self.score,
            "sources": list(self.sources),
            "reasons": list(self.reasons),
            "graph_node": self.graph_node,
            "neighbors": list(self.neighbors),
        }


@dataclass(slots=True, frozen=True)
class RetrievalResult:
    query: str
    intent: str
    hits: tuple[RetrievalHit, ...]
    candidate_counts: dict[str, int]
    vector_status: str = "not_requested"
    reranker: str = "deterministic"

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "intent": self.intent,
            "candidate_counts": self.candidate_counts,
            "vector_status": self.vector_status,
            "reranker": self.reranker,
            "hits": [hit.as_dict() for hit in self.hits],
        }


@dataclass(slots=True, frozen=True)
class EmbeddingRunResult:
    total_docs: int
    already_embedded: int
    embedded: int
    skipped_empty: int
    model: str
    dims: int
    limit_hit: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_docs": self.total_docs,
            "already_embedded": self.already_embedded,
            "embedded": self.embedded,
            "skipped_empty": self.skipped_empty,
            "model": self.model,
            "dims": self.dims,
            "limit_hit": self.limit_hit,
        }


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
        try:
            self._ensure_retrieval_fts_schema()
        except sqlite3.OperationalError:
            self._fts_enabled = False
        self.conn.commit()

    def _ensure_retrieval_document_columns(self) -> None:
        columns = _table_columns(self.conn, "retrieval_documents")
        migrations = {
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
                  title, body, body_char_count, chunk_index, chunk_count,
                  memory_class, importance, metadata_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doc_id) DO UPDATE SET
                  doc_type=excluded.doc_type,
                  graph_node_id=excluded.graph_node_id,
                  node_kind=excluded.node_kind,
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

    def replace_documents(self, docs: Iterable[RetrievalDocument]) -> int:
        self.conn.execute("DELETE FROM retrieval_documents")
        if self._fts_enabled:
            self.conn.execute("DELETE FROM retrieval_documents_fts")
        self.conn.commit()
        return self.upsert_documents(docs)

    def list_documents(self, *, limit: int = 10000) -> list[RetrievalDocument]:
        rows = self.conn.execute(
            """
            SELECT * FROM retrieval_documents
            ORDER BY doc_type, graph_node_id, chunk_index
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        return [_doc_from_row(row) for row in rows]

    def get_documents_by_ids(self, doc_ids: Iterable[str]) -> dict[str, RetrievalDocument]:
        ids = list(dict.fromkeys(doc_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders})",
            ids,
        ).fetchall()
        return {str(row["doc_id"]): _doc_from_row(row) for row in rows}

    def documents_by_graph_node_ids(self, node_ids: Iterable[str]) -> dict[str, list[RetrievalDocument]]:
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders})",
            ids,
        ).fetchall()
        out: dict[str, list[RetrievalDocument]] = {}
        for row in rows:
            doc = _doc_from_row(row)
            out.setdefault(doc.graph_node_id, []).append(doc)
        return out

    def bm25_search(self, query: str, *, limit: int = 50) -> list[RetrievalCandidate]:
        fts_query = _fts_query(query)
        if not fts_query:
            return []
        if not self._fts_enabled:
            return self.like_search(query, limit=limit)
        try:
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
            return self.like_search(query, limit=limit)
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

    def like_search(self, query: str, *, limit: int = 50) -> list[RetrievalCandidate]:
        terms = sorted(_terms(query))[:8]
        if not terms:
            return []
        rows = self.list_documents(limit=10000)
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

    def exact_search(self, query: str, *, limit: int = 50) -> list[RetrievalCandidate]:
        tokens = _exact_tokens(query)
        if not tokens:
            return []
        candidates: list[tuple[float, RetrievalDocument]] = []
        for doc in self.list_documents(limit=10000):
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


def build_retrieval_documents_from_graph(
    graph_store: GraphStore,
    *,
    session_id: str = "",
    kinds: list[str] | None = None,
    node_limit: int = 10000,
    max_doc_chars: int = 5000,
    pipeline_version: str = "",
    graph_schema_version: str = "",
) -> list[RetrievalDocument]:
    docs: list[RetrievalDocument] = []

    if kinds is not None:
        return _documents_for_nodes(
            graph_store.list_nodes(limit=node_limit, kinds=kinds, session_id=session_id),
            max_doc_chars=max_doc_chars,
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
        )

    active_graph_commit_id = _active_graph_commit_id(graph_store)
    if active_graph_commit_id:
        central_docs = _documents_for_nodes(
            (
                node
                for node in graph_store.list_nodes(
                    limit=node_limit,
                    kinds=list(CENTRAL_RETRIEVAL_NODE_KINDS),
                    session_id=session_id,
                )
                if _is_active_central_node(node, active_graph_commit_id)
            ),
            max_doc_chars=max_doc_chars,
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
        )
        if central_docs:
            docs.extend(central_docs)

    docs.extend(
        _documents_for_nodes(
            graph_store.list_nodes(
                limit=max(node_limit, 1),
                kinds=list(SESSION_RETRIEVAL_NODE_KINDS),
                session_id=session_id,
            ),
            max_doc_chars=max_doc_chars,
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
        )
    )
    return docs


def _documents_for_nodes(
    nodes: Iterable[dict[str, Any]],
    *,
    max_doc_chars: int,
    pipeline_version: str = "",
    graph_schema_version: str = "",
) -> list[RetrievalDocument]:
    docs: list[RetrievalDocument] = []
    for node in nodes:
        if pipeline_version and _node_version_value(node, "pipeline_version") != pipeline_version:
            continue
        if graph_schema_version and _node_version_value(node, "graph_schema_version") != graph_schema_version:
            continue
        docs.extend(_documents_for_node(node, max_doc_chars=max_doc_chars))
    return docs


def _active_graph_commit_id(graph_store: GraphStore) -> str:
    for node in graph_store.list_nodes(limit=100, kinds=["GraphView"]):
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        branch = str(metadata.get("branch") or "")
        mode = str(metadata.get("mode") or "")
        status = str(node.get("status") or metadata.get("status") or "")
        if branch == "main" and mode == "active" and status == "active":
            return str(metadata.get("graph_commit_id") or "")
    return ""


def _is_active_central_node(node: dict[str, Any], active_graph_commit_id: str) -> bool:
    node_kind = str(node.get("kind") or "")
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    graph_commit_id = str(metadata.get("graph_commit_id") or "")
    if node_kind == "GraphView":
        return str(metadata.get("branch") or "") == "main" and str(metadata.get("mode") or "") == "active"
    if node_kind == "GraphCommit":
        return str(node.get("id") or "") == active_graph_commit_id or graph_commit_id == active_graph_commit_id
    if node_kind in {"KnowledgeAtom", "KnowledgeVersion"}:
        return graph_commit_id == active_graph_commit_id and str(node.get("status") or metadata.get("status") or "active") == "active"
    return False


def embed_missing_retrieval_documents(
    *,
    index_store: RetrievalIndexStore,
    embedding_store: GraphEmbeddingStore,
    embedder: TextEmbeddingProvider,
    model: str,
    graph_scope: str,
    session_id: str = "",
    extraction_run_id: str = "",
    limit: int = 0,
    embedding_kind: str = RETRIEVAL_EMBEDDING_KIND,
) -> EmbeddingRunResult:
    docs = index_store.list_documents(limit=100000)
    existing = embedding_store.list_records(
        embedding_kind=embedding_kind,
        model=model,
        graph_scope=graph_scope,
        status="active",
        limit=100000,
    )
    existing_hashes = {(record.graph_path, record.content_hash) for record in existing}
    embedded = 0
    already = 0
    skipped_empty = 0
    dims = 0
    limit_hit = False
    for doc in docs:
        text = doc.embedding_text()
        if not text.strip():
            skipped_empty += 1
            continue
        content_hash = hash_content(text)
        if (doc.doc_id, content_hash) in existing_hashes:
            already += 1
            continue
        if limit and embedded >= limit:
            limit_hit = True
            break
        vector = [float(value) for value in embedder.embed(text)]
        if not vector:
            skipped_empty += 1
            continue
        dims = dims or len(vector)
        record = GraphEmbeddingRecord.create(
            node_id=doc.graph_node_id,
            node_kind=doc.node_kind,
            memory_class=doc.memory_class,
            graph_scope=graph_scope,
            graph_path=doc.doc_id,
            session_id=session_id,
            extraction_run_id=extraction_run_id,
            embedding_kind=embedding_kind,
            model=model,
            text=text,
            vector=vector,
            importance=doc.importance,
            memory_tier="hot",
            status="active",
        )
        embedding_store.upsert(record)
        existing_hashes.add((doc.doc_id, content_hash))
        embedded += 1
    return EmbeddingRunResult(
        total_docs=len(docs),
        already_embedded=already,
        embedded=embedded,
        skipped_empty=skipped_empty,
        model=model,
        dims=dims,
        limit_hit=limit_hit,
    )


def retrieve_session_graph(
    *,
    query: str,
    index_store: RetrievalIndexStore,
    graph_store: GraphStore,
    embedding_store: GraphEmbeddingStore | None = None,
    embedder: TextEmbeddingProvider | None = None,
    embedding_model: str = "",
    graph_scope: str = "",
    session_id: str = "",
    limit: int = 10,
    candidate_limit: int = 80,
    expand_neighbors: int = 12,
    embedding_kind: str = RETRIEVAL_EMBEDDING_KIND,
    require_vector: bool = False,
    bi_encoder_weight: float = 0.2,
    reranker_backend: str = "disabled",
    reranker_model: str = "",
    rerank_top_k: int = 50,
    rerank_max_chars: int = 1800,
    include_graph_nodes: bool = True,
) -> RetrievalResult:
    intent = classify_query(query)
    exact = index_store.exact_search(query, limit=candidate_limit)
    bm25 = index_store.bm25_search(query, limit=candidate_limit)
    vector, vector_status = _vector_candidates(
        query=query,
        index_store=index_store,
        embedding_store=embedding_store,
        embedder=embedder,
        embedding_model=embedding_model,
        graph_scope=graph_scope,
        candidate_limit=candidate_limit,
        embedding_kind=embedding_kind,
    )
    if require_vector and not vector:
        raise ValueError(f"vector retrieval required but returned no candidates (status={vector_status})")
    candidate_sets = {"exact": exact, "bm25": bm25, "vector": vector}
    source_scores = _candidate_raw_scores(candidate_sets)
    fused = _rrf_fuse(candidate_sets)
    docs_by_id = index_store.get_documents_by_ids(doc_id for doc_id, _score, _sources in fused)
    ranked: list[tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]] = []
    for doc_id, fused_score, sources in fused:
        doc = docs_by_id.get(doc_id)
        if doc is None:
            continue
        neighbors = tuple(graph_store.neighbors(doc.graph_node_id, limit=expand_neighbors)) if expand_neighbors else ()
        final_score, reasons = _rerank_document(
            query=query,
            intent=intent,
            doc=doc,
            fused_score=fused_score,
            neighbors=neighbors,
            source_scores=source_scores.get(doc_id, {}),
            bi_encoder_weight=bi_encoder_weight,
        )
        ranked.append((doc, final_score, sources, tuple(reasons), neighbors))
    ranked.sort(key=lambda item: item[1], reverse=True)
    ranked, reranker_label = _cross_encoder_rerank(
        query=query,
        intent=intent,
        ranked=ranked,
        backend=reranker_backend,
        model_name=reranker_model,
        top_k=rerank_top_k,
        max_chars=rerank_max_chars,
    )

    graph_nodes = (
        {str(node.get("id")): node for node in graph_store.list_nodes(limit=100000, session_id=session_id)}
        if include_graph_nodes
        else {}
    )
    hits = tuple(
        RetrievalHit(
            document=doc,
            score=round(score, 6),
            sources=sources,
            reasons=reasons,
            graph_node=_compact_output_node(graph_nodes.get(doc.graph_node_id, {})),
            neighbors=tuple(_compact_output_node(node) for node in neighbors),
        )
        for doc, score, sources, reasons, neighbors in ranked[: max(1, limit)]
    )
    return RetrievalResult(
        query=query,
        intent=intent,
        hits=hits,
        vector_status=vector_status,
        reranker=reranker_label or ("deterministic+bi_encoder" if vector else "deterministic"),
        candidate_counts={
            "exact": len(exact),
            "bm25": len(bm25),
            "vector": len(vector),
            "fused": len(fused),
        },
    )


def _cross_encoder_rerank(
    *,
    query: str,
    intent: str,
    ranked: list[tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]],
    backend: str,
    model_name: str,
    top_k: int,
    max_chars: int,
) -> tuple[
    list[tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]],
    str,
]:
    selected_backend = str(backend or "disabled").strip().lower()
    if selected_backend in {"", "disabled", "none"} or not ranked:
        return ranked, ""
    if selected_backend not in {"auto", "lexical", "cross-encoder"}:
        raise ValueError("reranker backend must be one of: disabled, auto, lexical, cross-encoder")
    rerank_count = max(1, min(int(top_k or 50), len(ranked)))
    candidates = [
        RerankCandidate(
            memory_id=doc.doc_id,
            text=_reranker_text(doc, neighbors, max_chars=max_chars),
        )
        for doc, _score, _sources, _reasons, neighbors in ranked[:rerank_count]
    ]
    reranked = rerank_candidates(
        query=query,
        candidates=candidates,
        backend=selected_backend,
        model_name=model_name,
        max_chars=max_chars,
    )
    by_doc_id = reranked.scores
    boosted: list[
        tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]
    ] = []
    cross_weight = _cross_encoder_weight(intent)
    for doc, score, sources, reasons, neighbors in ranked[:rerank_count]:
        cross_score = max(0.0, min(1.0, float(by_doc_id.get(doc.doc_id, 0.0))))
        new_reasons = [
            *reasons,
            f"{_safe_reranker_prefix(reranked.backend)}_score:{round(cross_score, 6)}",
            f"{_safe_reranker_prefix(reranked.backend)}_model:{reranked.model}",
            f"{_safe_reranker_prefix(reranked.backend)}_weight:{round(cross_weight, 3)}",
        ]
        if reranked.fallback_reason:
            new_reasons.append(f"reranker_fallback:{reranked.fallback_reason}")
        boosted.append((doc, score + cross_score * cross_weight, sources, tuple(new_reasons), neighbors))
    output = [*boosted, *ranked[rerank_count:]]
    output.sort(key=lambda item: item[1], reverse=True)
    base = "deterministic+bi_encoder" if any("vector" in item[2] for item in ranked) else "deterministic"
    suffix = "cross_encoder" if reranked.backend == "cross-encoder" else reranked.backend.replace("-", "_")
    if reranked.fallback_reason:
        suffix = f"{suffix}_fallback"
    return output, f"{base}+{suffix}"


def _cross_encoder_weight(intent: str) -> float:
    if intent == "decision_history":
        # Small code-oriented rerankers often over-score the literal word
        # "decision" and under-score graph-specific policy nodes such as
        # capture-only hooks. Keep them as a secondary signal for this intent.
        return 0.08
    return 0.45


def _reranker_text(
    doc: RetrievalDocument,
    neighbors: tuple[dict[str, Any], ...],
    *,
    max_chars: int,
) -> str:
    neighbor_text = "\n".join(
        f"{node.get('kind')}: {node.get('label') or ''} {node.get('summary') or ''}"
        for node in neighbors[:12]
    )
    text = "\n".join(
        part
        for part in (
            doc.title,
            doc.body,
            "metadata: " + json.dumps(doc.metadata, sort_keys=True),
            "neighbors:\n" + neighbor_text if neighbor_text else "",
        )
        if part
    )
    return text[: max(100, int(max_chars or 1800))]


def _safe_reranker_prefix(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") or "reranker"


def classify_query(query: str) -> str:
    lowered = query.lower()
    if re.search(r"\b(version flow|version history|version chain|symbol version|symbol history|show versions?)\b", lowered):
        return "version_flow"
    if "why" in lowered or "reason" in lowered:
        return "code_why"
    if "decision" in lowered or "decide" in lowered:
        return "decision_history"
    if "::" in query or re.search(r"\b[\w./-]+\.(py|js|ts|tsx|jsx|md)\b", lowered):
        return "version_flow"
    return "semantic_search"


def _documents_for_node(node: dict[str, Any], *, max_doc_chars: int) -> list[RetrievalDocument]:
    node_id = str(node.get("id") or "")
    node_kind = str(node.get("kind") or "GraphNode")
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    doc_type = _doc_type(node_kind)
    memory_class = _memory_class(doc_type, node_kind)
    packet_id = str(metadata.get("packet_id") or metadata.get("source_packet_id") or node.get("packet_id") or "")
    commit_sha = str(
        metadata.get("commit_sha")
        or metadata.get("source_commit_sha")
        or node.get("commit_sha")
        or node.get("commit_id")
        or ""
    )
    title = str(node.get("label") or node_id)
    body = _node_body(node)
    chunks = _chunk_text(body, max_doc_chars=max_doc_chars)
    out: list[RetrievalDocument] = []
    for index, chunk in enumerate(chunks, start=1):
        suffix = f":{index}" if len(chunks) > 1 else ""
        out.append(
            RetrievalDocument(
                doc_id=f"doc:{doc_type}:{node_id}{suffix}",
                doc_type=doc_type,
                graph_node_id=node_id,
                node_kind=node_kind,
                packet_id=packet_id,
                commit_sha=commit_sha,
                title=title,
                body=chunk,
                chunk_index=index,
                chunk_count=len(chunks),
                memory_class=memory_class,
                importance=_importance(doc_type, node_kind, metadata),
                metadata={"node_metadata": _retrieval_metadata(metadata), "chunked": len(chunks) > 1},
            )
        )
    return out


def _retrieval_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "atom_id",
        "atom_kind",
        "branch",
        "canonical_key",
        "canonical_key_version",
        "packet_id",
        "source_packet_id",
        "commit_sha",
        "source_commit_sha",
        "node_type",
        "subject",
        "statement",
        "reason",
        "file_path",
        "symbol",
        "symbol_id",
        "changed_files",
        "paths",
        "evidence_refs",
        "version_count",
        "graph_commit_id",
        "idempotency_key",
        "job_id",
        "merge_plan_id",
        "mode",
        "repo_id",
        "source_node_ids",
        "status",
        "pipeline_version",
        "graph_schema_version",
        "version_metadata",
    }
    return {key: metadata[key] for key in keep if key in metadata}


def _node_version_value(node: dict[str, Any], key: str) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get(key) or metadata.get(key) or "")


def _compact_output_node(node: dict[str, Any]) -> dict[str, Any]:
    if not node:
        return {}
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    compact_metadata = _retrieval_metadata(metadata)
    for key in ("path", "qualified_name", "symbol_kind", "line_start", "line_end", "hunk_ids", "version_count"):
        if key in metadata:
            compact_metadata[key] = metadata[key]
    return {
        "id": node.get("id"),
        "kind": node.get("kind"),
        "label": node.get("label"),
        "summary": _clip(str(node.get("summary") or ""), 500),
        "status": node.get("status"),
        "session_id": node.get("session_id"),
        "evidence_id": node.get("evidence_id"),
        "commit_id": node.get("commit_id"),
        "packet_id": node.get("packet_id") or metadata.get("packet_id") or metadata.get("source_packet_id"),
        "commit_sha": node.get("commit_sha") or metadata.get("commit_sha") or metadata.get("source_commit_sha"),
        "metadata": compact_metadata,
    }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 13)].rstrip() + " ... <clipped>"


def _node_body(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    fields = [
        f"kind: {node.get('kind') or ''}",
        f"status: {node.get('status') or ''}",
        f"summary: {node.get('summary') or ''}",
        f"commit: {node.get('commit_id') or metadata.get('commit_sha') or ''}",
        f"packet: {metadata.get('packet_id') or metadata.get('source_packet_id') or ''}",
    ]
    for key in (
        "atom_kind",
        "canonical_key",
        "atom_id",
        "graph_commit_id",
        "merge_plan_id",
        "repo_id",
        "source_node_ids",
        "version_metadata",
    ):
        if key in metadata:
            value = metadata[key]
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, sort_keys=True)
            fields.append(f"{key}: {value}")
    for key in (
        "node_type",
        "subject",
        "statement",
        "reason",
        "file_path",
        "symbol",
        "symbol_id",
        "changed_files",
        "paths",
        "evidence_refs",
    ):
        if key in metadata:
            value = metadata[key]
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, sort_keys=True)
            fields.append(f"{key}: {value}")
    fields.append("metadata: " + json.dumps(metadata, sort_keys=True))
    return "\n".join(str(field) for field in fields if str(field).strip())


def _chunk_text(text: str, *, max_doc_chars: int) -> list[str]:
    if len(text) <= max_doc_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > max_doc_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if line_len > max_doc_chars:
            for start in range(0, len(line), max_doc_chars):
                part = line[start : start + max_doc_chars]
                if part:
                    chunks.append(part)
            continue
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:max_doc_chars]]


def _vector_candidates(
    *,
    query: str,
    index_store: RetrievalIndexStore,
    embedding_store: GraphEmbeddingStore | None,
    embedder: TextEmbeddingProvider | None,
    embedding_model: str,
    graph_scope: str,
    candidate_limit: int,
    embedding_kind: str,
) -> tuple[list[RetrievalCandidate], str]:
    if embedding_store is None or embedder is None or not embedding_model:
        return [], "not_requested"
    query_vector = embedder.embed(query)
    hits, status = embedding_store.search(
        query_vector,
        embedding_kind=embedding_kind,
        model=embedding_model,
        graph_scope=graph_scope,
        limit=candidate_limit,
    )
    doc_ids: list[str] = []
    scores: dict[str, float] = {}
    hits_without_graph_path: list[GraphEmbeddingHit] = []
    for hit in hits:
        if hit.graph_path:
            doc_ids.append(hit.graph_path)
            scores[hit.graph_path] = max(scores.get(hit.graph_path, 0.0), hit.score)
        else:
            hits_without_graph_path.append(hit)
    if hits_without_graph_path:
        docs_by_node = index_store.documents_by_graph_node_ids(hit.node_id for hit in hits_without_graph_path)
        for hit in hits_without_graph_path:
            for doc in docs_by_node.get(hit.node_id, [])[:1]:
                doc_ids.append(doc.doc_id)
                scores[doc.doc_id] = max(scores.get(doc.doc_id, 0.0), hit.score)
    return (
        [
            RetrievalCandidate(doc_id, "vector", rank, scores.get(doc_id, 0.0))
            for rank, doc_id in enumerate(dict.fromkeys(doc_ids), start=1)
        ],
        status,
    )


def _rrf_fuse(candidate_sets: dict[str, list[RetrievalCandidate]], *, k: int = 60) -> list[tuple[str, float, tuple[str, ...]]]:
    scores: dict[str, float] = {}
    sources: dict[str, set[str]] = {}
    for source, candidates in candidate_sets.items():
        for candidate in candidates:
            scores[candidate.doc_id] = scores.get(candidate.doc_id, 0.0) + (1.0 / (k + candidate.rank))
            sources.setdefault(candidate.doc_id, set()).add(source)
    return [
        (doc_id, score, tuple(sorted(sources.get(doc_id, set()))))
        for doc_id, score in sorted(scores.items(), key=lambda item: item[1], reverse=True)
    ]


def _candidate_raw_scores(candidate_sets: dict[str, list[RetrievalCandidate]]) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = {}
    for source, candidates in candidate_sets.items():
        for candidate in candidates:
            source_scores = scores.setdefault(candidate.doc_id, {})
            source_scores[source] = max(float(candidate.raw_score), source_scores.get(source, float("-inf")))
    return scores


def _rerank_document(
    *,
    query: str,
    intent: str,
    doc: RetrievalDocument,
    fused_score: float,
    neighbors: tuple[dict[str, Any], ...],
    source_scores: dict[str, float],
    bi_encoder_weight: float,
) -> tuple[float, list[str]]:
    terms = _terms(query)
    scoring_terms = terms
    if intent == "version_flow":
        # "version flow" is an operator phrase. Rank by the requested symbol/path
        # terms, otherwise functions named "version_flow" beat the actual symbol.
        scoring_terms = terms.difference(VERSION_FLOW_OPERATOR_TERMS) or terms
    text = _normalize(f"{doc.title} {doc.body} {json.dumps(doc.metadata, sort_keys=True)}")
    primary_text = _primary_rank_text(doc, include_code_locator_context=_query_has_code_locator(query))
    reasons = [f"fused:{round(fused_score, 6)}"]
    score = fused_score
    overlap = [term for term in scoring_terms if term in text]
    if overlap:
        overlap_ratio = len(overlap) / max(1, len(scoring_terms))
        score += min(0.4, overlap_ratio * 0.4)
        reasons.append("term_overlap:" + ",".join(overlap[:8]))
    topic_terms = _topic_terms(query, intent)
    topic_overlap_ratio = 0.0
    if topic_terms:
        topic_overlap = [term for term in topic_terms if term in primary_text]
        if topic_overlap:
            topic_overlap_ratio = len(topic_overlap) / max(1, len(topic_terms))
            score += min(0.5, topic_overlap_ratio * 0.5)
            reasons.append("topic_focus_overlap:" + ",".join(topic_overlap[:8]))
        elif intent in {"code_why", "decision_history"}:
            score -= 0.18
            reasons.append("topic_focus_penalty")
    if doc.doc_type == "central_version":
        central_boost = _central_version_boost(doc, intent=intent, query=query, topic_overlap_ratio=topic_overlap_ratio)
        score += central_boost
        if central_boost:
            reasons.append(f"central_active_boost:{round(central_boost, 3)}")
        else:
            score -= 0.05
            reasons.append("central_low_topic_overlap_penalty")
    elif doc.doc_type == "central_atom":
        if topic_overlap_ratio >= 0.4 or intent == "version_flow" or _query_has_code_locator(query):
            score += 0.10
            reasons.append("central_atom_context_boost")
    elif doc.doc_type == "graph_lineage" and intent not in {"version_flow"}:
        score -= 0.12
        reasons.append("graph_lineage_penalty")
    if intent in {"code_why", "decision_history"} and doc.doc_type == "reasoning":
        score += 0.25
        reasons.append("reasoning_boost")
    node_type = _doc_node_type(doc)
    if intent == "decision_history" and doc.doc_type == "reasoning":
        if node_type == "Decision":
            score += 0.18
            reasons.append("decision_node_boost")
        elif node_type in {"Cause", "Fix", "Constraint"}:
            score += 0.08
            reasons.append("decision_context_boost")
    if "vector" in source_scores:
        vector_score = max(0.0, min(1.0, float(source_scores["vector"])))
        vector_boost = vector_score * max(0.0, float(bi_encoder_weight))
        score += vector_boost
        reasons.append(f"bi_encoder_score:{round(vector_score, 6)}")
        reasons.append(f"bi_encoder_boost:{round(vector_boost, 6)}")
    if intent == "version_flow" and doc.doc_type in {"symbol", "code"}:
        score += 0.25
        reasons.append("version_flow_boost")
        if overlap:
            target_ratio = len(overlap) / max(1, len(scoring_terms))
            score += target_ratio * 0.2
            reasons.append(f"version_target_overlap:{round(target_ratio, 3)}")
        if doc.doc_type == "symbol":
            score += 0.1
            reasons.append("symbol_version_boost")
    if doc.memory_class == "supporting_evidence":
        score -= 0.18
        reasons.append("supporting_evidence_penalty")
    if doc.doc_type == "commit":
        score -= 0.12
        reasons.append("commit_hub_penalty")
    if _looks_like_test_artifact(doc) and "test" not in terms:
        score -= 0.08
        reasons.append("test_artifact_penalty")
    neighbor_text = _normalize(" ".join(f"{n.get('label') or ''} {n.get('summary') or ''}" for n in neighbors))
    if neighbor_text and any(term in neighbor_text for term in terms):
        score += 0.08
        reasons.append("neighbor_overlap")
    score += min(max(doc.importance, 0.0), 1.0) * 0.05
    return score, reasons


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
        chunk_index=int(row["chunk_index"]),
        chunk_count=int(row["chunk_count"]),
        memory_class=str(row["memory_class"]),
        importance=float(row["importance"]),
        metadata=metadata,
    )


def _table_columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return ()
    return tuple(str(row["name"]) for row in rows)


def _doc_type(node_kind: str) -> str:
    if node_kind == "KnowledgeVersion":
        return "central_version"
    if node_kind == "KnowledgeAtom":
        return "central_atom"
    if node_kind in {"GraphCommit", "GraphView"}:
        return "graph_lineage"
    if node_kind in {"ReasoningNode", "DecisionUnit", "DecisionThread"}:
        return "reasoning"
    if node_kind in {"WorkChange", "GitCommit", "Commit"}:
        return "commit"
    if node_kind in {"CodeNode", "CodeHunk"}:
        return "code"
    if node_kind in {"Symbol", "SymbolVersion"}:
        return "symbol"
    if node_kind in {"EvidenceRef", "Evidence", "ToolFact"}:
        return "evidence"
    return "graph"


def _memory_class(doc_type: str, node_kind: str) -> str:
    if doc_type == "central_version":
        return "central_active_memory"
    if doc_type == "central_atom":
        return "central_canonical_atom"
    if doc_type == "graph_lineage":
        return "graph_lineage"
    if doc_type == "reasoning":
        return "answer_grade_reasoning"
    if doc_type == "code":
        return "code_change"
    if doc_type == "symbol":
        return "symbol_version"
    if doc_type == "commit" or node_kind == "WorkChange":
        return "work_change"
    if doc_type == "evidence":
        return "supporting_evidence"
    return "graph_context"


def _importance(doc_type: str, node_kind: str, metadata: dict[str, Any]) -> float:
    if isinstance(metadata.get("importance"), (int, float)):
        return float(metadata["importance"])
    if doc_type == "central_version":
        return 0.95
    if doc_type == "central_atom":
        return 0.75
    if doc_type == "graph_lineage":
        return 0.25
    if doc_type == "reasoning":
        return 0.9
    if node_kind == "WorkChange" or doc_type == "commit":
        return 0.8
    if doc_type == "code":
        return 0.7
    if doc_type == "symbol":
        return 0.65
    if doc_type == "evidence":
        return 0.35
    return 0.5


def _fts_query(query: str) -> str:
    terms = sorted(_expanded_query_terms(query))[:12]
    return " OR ".join(terms)


def _exact_tokens(query: str) -> list[str]:
    tokens = []
    for token in re.findall(r"[A-Za-z0-9_./:-]+", query):
        if len(token) <= 2:
            continue
        if token.lower() in QUERY_STOPWORDS:
            continue
        if "/" in token or "\\" in token or "." in token or "::" in token or re.fullmatch(r"[0-9a-f]{6,40}", token):
            tokens.append(token.replace("\\", "/"))
    return tokens


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in re.split(r"[^a-zA-Z0-9_]+", text.lower()):
        if len(token) <= 2 or token in QUERY_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        terms.add(_stem_term(token))
    return terms


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-zA-Z0-9_./:-]+", str(text).lower()))


def _stem_term(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 4 and token.endswith("es"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s"):
        return token[:-1]
    return token


def _topic_terms(query: str, intent: str) -> set[str]:
    terms = set(_expanded_query_terms(query))
    if "hook" in terms:
        # In AMO queries, "Codex hooks" usually names the agent surface.
        # The durable topic is the hook behavior: capture, injection, prompt flow.
        terms = terms.difference(AGENT_CONTEXT_TERMS)
    if intent == "decision_history":
        return terms.difference(DECISION_HISTORY_OPERATOR_TERMS)
    if intent == "code_why":
        return terms.difference(CODE_WHY_OPERATOR_TERMS)
    if intent == "version_flow":
        return terms.difference(VERSION_FLOW_OPERATOR_TERMS)
    return terms


def _expanded_query_terms(query: str) -> set[str]:
    terms = _terms(query)
    if "hook" in terms:
        terms.update(HOOK_QUERY_EXPANSION_TERMS)
    return terms


def _query_has_code_locator(query: str) -> bool:
    lowered = query.lower()
    return bool(
        "::" in query
        or re.search(r"\b[\w./-]+\.(py|js|ts|tsx|jsx|md|toml|json|yaml|yml)\b", lowered)
        or re.search(r"\b[a-z0-9]+_[a-z0-9_]+\b", lowered)
    )


def _primary_rank_text(doc: RetrievalDocument, *, include_code_locator_context: bool = False) -> str:
    if doc.doc_type != "reasoning":
        return _normalize(f"{doc.title} {doc.body}")

    kept: list[str] = [doc.title]
    for raw_line in doc.body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        prefix = line.split(":", 1)[0].strip().lower()
        if prefix in {"changed paths", "linked code", "evidence", "metadata", "paths", "file_path", "symbol"} and not include_code_locator_context:
            continue
        kept.append(line)
    return _normalize(" ".join(kept))


def _doc_node_type(doc: RetrievalDocument) -> str:
    metadata = doc.metadata.get("node_metadata") if isinstance(doc.metadata, dict) else None
    if isinstance(metadata, dict):
        return str(metadata.get("node_type") or "")
    return str(doc.metadata.get("node_type") or "") if isinstance(doc.metadata, dict) else ""


def _central_version_boost(
    doc: RetrievalDocument,
    *,
    intent: str,
    query: str,
    topic_overlap_ratio: float,
) -> float:
    metadata = doc.metadata.get("node_metadata") if isinstance(doc.metadata, dict) else {}
    atom_kind = str(metadata.get("atom_kind") or "") if isinstance(metadata, dict) else ""
    if atom_kind in {"decision", "problem"}:
        return 0.45
    if intent == "version_flow" or _query_has_code_locator(query):
        return 0.55
    if topic_overlap_ratio >= 0.6:
        return 0.45
    if topic_overlap_ratio >= 0.4:
        return 0.18
    return 0.0


def _looks_like_test_artifact(doc: RetrievalDocument) -> bool:
    lowered = f"{doc.title} {doc.body}".lower()
    return "tests/" in lowered or "tests\\" in lowered or "test_" in lowered
