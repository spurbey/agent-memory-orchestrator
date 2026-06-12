from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..projection import HarnessProjectionDocument


@dataclass(slots=True, frozen=True)
class LexicalRetrievalOptions:
    top_k: int = 8
    min_score: float = 0.12
    k1: float = 1.2
    b: float = 0.75


@dataclass(slots=True, frozen=True)
class LexicalRetrievalHit:
    document: HarnessProjectionDocument
    score: float
    normalized_score: float
    matched_terms: tuple[str, ...]
    term_scores: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.document.doc_id,
            "source_node_id": self.document.source_node_id,
            "source_kind": self.document.source_kind,
            "doc_type": self.document.doc_type,
            "title": self.document.title,
            "score": self.score,
            "normalized_score": self.normalized_score,
            "matched_terms": list(self.matched_terms),
            "term_scores": dict(self.term_scores),
        }


__all__ = ["LexicalRetrievalHit", "LexicalRetrievalOptions"]
