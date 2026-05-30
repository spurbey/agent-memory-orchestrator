from __future__ import annotations

from ...domain.retrieval.models import RetrievalResult
from ...domain.retrieval.models import TextEmbeddingProvider
from ...graph.store import GraphStore
from ...infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ...reasoning_graph.embedding_store import GraphEmbeddingStore
from ...reasoning_graph.retrieval import RETRIEVAL_EMBEDDING_KIND
from ...reasoning_graph.retrieval import retrieve_session_graph

__all__ = ["RetrievalQueryService", "retrieve_session_graph"]


class RetrievalQueryService:
    """Application boundary for retrieval query execution."""

    def __init__(
        self,
        *,
        index_store: RetrievalIndexStore,
        graph_store: GraphStore,
        embedding_store: GraphEmbeddingStore | None = None,
        embedder: TextEmbeddingProvider | None = None,
    ) -> None:
        self.index_store = index_store
        self.graph_store = graph_store
        self.embedding_store = embedding_store
        self.embedder = embedder

    def retrieve(
        self,
        *,
        query: str,
        embedding_model: str = "",
        graph_scope: str = "",
        session_id: str = "",
        repo_id: str = "",
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
        return retrieve_session_graph(
            query=query,
            index_store=self.index_store,
            graph_store=self.graph_store,
            embedding_store=self.embedding_store,
            embedder=self.embedder,
            embedding_model=embedding_model,
            graph_scope=graph_scope,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            candidate_limit=candidate_limit,
            expand_neighbors=expand_neighbors,
            embedding_kind=embedding_kind,
            require_vector=require_vector,
            bi_encoder_weight=bi_encoder_weight,
            reranker_backend=reranker_backend,
            reranker_model=reranker_model,
            rerank_top_k=rerank_top_k,
            rerank_max_chars=rerank_max_chars,
            include_graph_nodes=include_graph_nodes,
        )
