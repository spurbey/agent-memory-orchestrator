from __future__ import annotations

from ...domain.retrieval.models import EmbeddingRunResult
from ...domain.retrieval.models import TextEmbeddingProvider
from ...infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ...infrastructure.faiss.embedding_store import GraphEmbeddingRecord
from ...infrastructure.faiss.embedding_store import GraphEmbeddingStore
from ...infrastructure.faiss.embedding_store import hash_content


RETRIEVAL_EMBEDDING_KIND = "retrieval_text"


def embed_missing_retrieval_documents(
    *,
    index_store: RetrievalIndexStore,
    embedding_store: GraphEmbeddingStore,
    embedder: TextEmbeddingProvider,
    model: str,
    graph_scope: str,
    session_id: str = "",
    repo_id: str = "",
    extraction_run_id: str = "",
    limit: int = 0,
    embedding_kind: str = RETRIEVAL_EMBEDDING_KIND,
) -> EmbeddingRunResult:
    docs = index_store.list_documents(limit=100000, repo_id=repo_id)
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
        embedding_store.mark_stale_for_graph_path(
            graph_path=doc.doc_id,
            embedding_kind=embedding_kind,
            model=model,
            graph_scope=graph_scope,
            keep_content_hash=record.content_hash,
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


__all__ = ["RETRIEVAL_EMBEDDING_KIND", "embed_missing_retrieval_documents"]

