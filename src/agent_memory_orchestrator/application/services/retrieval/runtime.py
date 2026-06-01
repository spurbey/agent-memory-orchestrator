from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....core.db import connect
from ....domain.retrieval.answer import _answer_from_retrieval_result
from ....domain.retrieval.projection import build_retrieval_documents_from_graph
from ....infrastructure.faiss.embedding_store import GraphEmbeddingStore
from ....infrastructure.kuzu import GraphStore
from ....infrastructure.llm.text_embedder import StrictTextEmbedder
from ....infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ....llm.embeddings import embed_text
from .answer_trace import _central_answer_trace_from_retrieval
from .embedding import RETRIEVAL_EMBEDDING_KIND
from .embedding import embed_missing_retrieval_documents
from .query import retrieve_session_graph as retrieve_indexed_session_graph


def rebuild_retrieval_index(
    *,
    settings: Settings,
    graph_store: GraphStore,
    db_path: Path | None = None,
    session_id: str = "",
    repo_id: str = "",
    limit: int = 10000,
    max_doc_chars: int = 5000,
) -> dict[str, Any]:
    target_db = retrieval_db_path(settings, db_path)
    conn = connect(target_db)
    try:
        index = RetrievalIndexStore(conn)
        docs = build_retrieval_documents_from_graph(
            graph_store,
            session_id=session_id,
            repo_id=repo_id,
            node_limit=max(1, min(100000, int(limit))),
            max_doc_chars=max(1000, int(max_doc_chars)),
        )
        written = index.replace_documents(docs)
        return {
            "ok": True,
            "db_path": str(target_db),
            "graph_path": str(settings.graph_path),
            "session_id": session_id,
            "repo_id": repo_id,
            "retrieval_document_count": written,
            "doc_type_counts": _count_by(docs, "doc_type"),
            "node_kind_counts": _count_by(docs, "node_kind"),
        }
    finally:
        conn.close()


def embed_retrieval_index(
    *,
    settings: Settings,
    db_path: Path | None = None,
    session_id: str = "",
    repo_id: str = "",
    limit: int = 0,
    model: str = "",
    graph_scope: str = "",
    rebuild_faiss: bool = True,
) -> dict[str, Any]:
    target_db = retrieval_db_path(settings, db_path)
    embedding_model = model or settings.embedding_model
    scope = graph_scope or settings.retrieval_graph_scope or graph_scope_for_path(settings.graph_path)
    conn = connect(target_db)
    try:
        index = RetrievalIndexStore(conn)
        embedding_store = GraphEmbeddingStore(conn, db_path=target_db)
        embedder = text_embedder_for_model(embedding_model, dims=settings.embedding_dims)
        result = embed_missing_retrieval_documents(
            index_store=index,
            embedding_store=embedding_store,
            embedder=embedder,
            model=embedding_model,
            graph_scope=scope,
            session_id=session_id,
            repo_id=repo_id,
            extraction_run_id="graph_retrieval_index",
            limit=max(0, int(limit)),
        )
        faiss = (
            embedding_store.build_faiss_cache(
                embedding_kind="retrieval_text",
                model=embedding_model,
                graph_scope=scope,
            ).as_dict()
            if rebuild_faiss
            else {"status": "skipped", "reason": "disabled"}
        )
        return {
            "ok": True,
            "db_path": str(target_db),
            "graph_path": str(settings.graph_path),
            "graph_scope": scope,
            "repo_id": repo_id,
            "embedding": result.as_dict(),
            "faiss": faiss,
        }
    finally:
        conn.close()


def retrieve_indexed_graph(
    *,
    settings: Settings,
    graph_store: GraphStore,
    query: str,
    db_path: Path | None = None,
    session_id: str = "",
    repo_id: str = "",
    limit: int = 8,
    use_vector: bool = True,
    model: str = "",
    graph_scope: str = "",
    require_vector: bool = False,
    include_answer: bool = True,
) -> dict[str, Any]:
    query = str(query or "").strip()
    if not query:
        raise ValueError("query is required")
    target_db = retrieval_db_path(settings, db_path)
    embedding_model = model or settings.embedding_model
    conn = connect(target_db)
    try:
        scope = resolve_retrieval_graph_scope(
            conn,
            requested_scope=graph_scope or settings.retrieval_graph_scope,
            default_scope=graph_scope_for_path(settings.graph_path),
            embedding_model=embedding_model,
        )
        index = RetrievalIndexStore(conn)
        if repo_id and not index.active_projection_id(repo_id):
            return {
                "ok": False,
                "error": "active_projection_missing",
                "db_path": str(target_db),
                "graph_path": str(settings.graph_path),
                "graph_scope": scope,
                "repo_id": repo_id,
                "retrieval": {
                    "query": query,
                    "hits": [],
                    "vector_status": "not_requested" if not use_vector else "unavailable",
                },
                "central_answer_trace": _central_answer_trace_from_retrieval(
                    settings,
                    repo_id=repo_id,
                    retrieval={"hits": []},
                    graph_store=graph_store,
                    warnings=["active_projection_missing"],
                ),
            }
        embedding_store: GraphEmbeddingStore | None = None
        embedder = None
        if use_vector and settings.vector_backend != "disabled":
            embedding_store = GraphEmbeddingStore(conn, db_path=target_db)
            embedder = text_embedder_for_model(embedding_model, dims=settings.embedding_dims)
        result = retrieve_indexed_session_graph(
            query=query,
            index_store=index,
            graph_store=graph_store,
            embedding_store=embedding_store,
            embedder=embedder,
            embedding_model=embedding_model if embedder is not None else "",
            graph_scope=scope,
            session_id=session_id,
            repo_id=repo_id,
            limit=max(1, min(50, int(limit))),
            expand_neighbors=12 if include_answer else 0,
            include_graph_nodes=include_answer,
            require_vector=require_vector,
            reranker_backend=settings.reranker_backend,
            reranker_model=settings.reranker_model,
            rerank_top_k=settings.rerank_top_k,
            rerank_max_chars=settings.rerank_max_chars,
        )
        payload = {
            "ok": True,
            "db_path": str(target_db),
            "graph_path": str(settings.graph_path),
            "graph_scope": scope,
            "repo_id": repo_id,
            "retrieval": result.as_dict(),
        }
        if repo_id:
            payload["central_answer_trace"] = _central_answer_trace_from_retrieval(
                settings,
                repo_id=repo_id,
                retrieval=result.as_dict(),
                graph_store=graph_store,
            )
        if include_answer:
            payload["answer"] = _answer_from_retrieval_result(
                result.as_dict(),
                graph_store=graph_store,
                session_id=session_id,
            )
        return payload
    finally:
        conn.close()


class HashTextEmbedder:
    def __init__(self, dims: int) -> None:
        self.dims = dims

    def embed(self, text: str) -> list[float]:
        return embed_text(text, self.dims)


def text_embedder_for_model(model: str, *, dims: int):
    if model.strip().lower() in {"hash", "hash-fallback", "deterministic", "local-hash"}:
        return HashTextEmbedder(dims)
    return StrictTextEmbedder(model)


def retrieval_db_path(settings: Settings, override: Path | None = None) -> Path:
    path = override or settings.retrieval_db_path or settings.db_path
    target = path if path.is_absolute() else (settings.home / path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def graph_scope_for_path(graph_path: Path) -> str:
    safe = re.sub(r"[^a-zA-Z0-9]+", "_", str(graph_path.resolve()).lower()).strip("_")
    return f"graph:{safe[-80:] or 'default'}"


def resolve_retrieval_graph_scope(
    conn: Any,
    *,
    requested_scope: str,
    default_scope: str,
    embedding_model: str,
) -> str:
    requested = str(requested_scope or "").strip()
    if not embedding_model:
        return requested or default_scope

    if requested:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM graph_embeddings
            WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
            """,
            (RETRIEVAL_EMBEDDING_KIND, embedding_model, requested),
        ).fetchone()
        if int(row["count"] if row else 0) > 0:
            return requested

    params = (RETRIEVAL_EMBEDDING_KIND, embedding_model, default_scope)
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM graph_embeddings
        WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
        """,
        params,
    ).fetchone()
    if int(row["count"] if row else 0) > 0:
        return default_scope

    fallback = conn.execute(
        """
        SELECT graph_scope, COUNT(*) AS count
        FROM graph_embeddings
        WHERE embedding_kind = ? AND model = ? AND status = 'active'
        GROUP BY graph_scope
        ORDER BY count DESC, graph_scope ASC
        LIMIT 1
        """,
        (RETRIEVAL_EMBEDDING_KIND, embedding_model),
    ).fetchone()
    return str(fallback["graph_scope"]) if fallback else requested or default_scope


def _count_by(items: list[Any], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, attr, "") or "")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
