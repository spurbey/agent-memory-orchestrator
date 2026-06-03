"""FAISS-backed vector cache adapters."""

from __future__ import annotations

from .embedding_store import GraphEmbeddingHit
from .embedding_store import GraphEmbeddingRecord
from .embedding_store import GraphEmbeddingStore
from .embedding_store import GraphFaissBuildResult
from .embedding_store import cosine_similarity
from .embedding_store import hash_content
from .embedding_store import make_embedding_id

__all__ = [
    "GraphEmbeddingHit",
    "GraphEmbeddingRecord",
    "GraphEmbeddingStore",
    "GraphFaissBuildResult",
    "cosine_similarity",
    "hash_content",
    "make_embedding_id",
]
