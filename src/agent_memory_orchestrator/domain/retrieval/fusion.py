from __future__ import annotations

from .models import RetrievalCandidate


def rrf_fuse(
    candidate_sets: dict[str, list[RetrievalCandidate]],
    *,
    k: int = 60,
) -> list[tuple[str, float, tuple[str, ...]]]:
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


def candidate_raw_scores(candidate_sets: dict[str, list[RetrievalCandidate]]) -> dict[str, dict[str, float]]:
    scores: dict[str, dict[str, float]] = {}
    for source, candidates in candidate_sets.items():
        for candidate in candidates:
            source_scores = scores.setdefault(candidate.doc_id, {})
            source_scores[source] = max(float(candidate.raw_score), source_scores.get(source, float("-inf")))
    return scores


__all__ = ["candidate_raw_scores", "rrf_fuse"]
