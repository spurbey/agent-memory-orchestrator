from __future__ import annotations

from ....domain.retrieval.models import RetrievalCandidate
from ....domain.retrieval.models import TextEmbeddingProvider
from ....infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ....infrastructure.faiss.embedding_store import GraphEmbeddingHit
from ....infrastructure.faiss.embedding_store import GraphEmbeddingStore


def vector_candidates(
    *,
    query: str,
    index_store: RetrievalIndexStore,
    embedding_store: GraphEmbeddingStore | None,
    embedder: TextEmbeddingProvider | None,
    embedding_model: str,
    graph_scope: str,
    candidate_limit: int,
    embedding_kind: str,
    repo_id: str = "",
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
        docs_by_node = index_store.documents_by_graph_node_ids(
            (hit.node_id for hit in hits_without_graph_path),
            repo_id=repo_id,
        )
        for hit in hits_without_graph_path:
            for doc in docs_by_node.get(hit.node_id, [])[:1]:
                doc_ids.append(doc.doc_id)
                scores[doc.doc_id] = max(scores.get(doc.doc_id, 0.0), hit.score)
    if repo_id:
        docs_by_id = index_store.get_documents_by_ids(doc_ids, repo_id=repo_id)
        doc_ids = [doc_id for doc_id in doc_ids if doc_id in docs_by_id]
    return (
        [
            RetrievalCandidate(doc_id, "vector", rank, scores.get(doc_id, 0.0))
            for rank, doc_id in enumerate(dict.fromkeys(doc_ids), start=1)
        ],
        status,
    )


__all__ = ["vector_candidates"]

