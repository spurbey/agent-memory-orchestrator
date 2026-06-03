from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


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
    repo_id: str = ""
    projection_id: str = ""
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
            "repo_id": self.repo_id,
            "projection_id": self.projection_id,
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


__all__ = [
    "EmbeddingRunResult",
    "RetrievalCandidate",
    "RetrievalDocument",
    "RetrievalHit",
    "RetrievalResult",
    "TextEmbeddingProvider",
]
