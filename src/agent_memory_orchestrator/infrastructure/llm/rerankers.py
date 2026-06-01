from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ...retrieval import lexical_rerank_score


@dataclass(slots=True, frozen=True)
class RerankCandidate:
    memory_id: str
    text: str


@dataclass(slots=True, frozen=True)
class RerankResult:
    scores: dict[str, float]
    backend: str
    model: str
    fallback_reason: str = ""


@lru_cache(maxsize=2)
def _load_cross_encoder(model_name: str):
    try:
        from sentence_transformers import CrossEncoder  # type: ignore
    except Exception:
        return None
    try:
        return CrossEncoder(model_name, local_files_only=True)
    except TypeError:
        return None
    except Exception:
        return None


def rerank_candidates(
    *,
    query: str,
    candidates: list[RerankCandidate],
    backend: str,
    model_name: str,
    max_chars: int,
) -> RerankResult:
    chosen = backend.strip().lower()
    if chosen == "lexical":
        return _lexical(query, candidates)
    if chosen == "cross-encoder":
        model = _load_cross_encoder(model_name)
        if model is None:
            raise RuntimeError(f"reranker model unavailable: {model_name}")
        return _cross_encoder(query, candidates, model, model_name, max_chars)
    if chosen == "auto":
        model = _load_cross_encoder(model_name)
        if model is None:
            result = _lexical(query, candidates)
            return RerankResult(result.scores, result.backend, result.model, f"cross_encoder_unavailable:{model_name}")
        return _cross_encoder(query, candidates, model, model_name, max_chars)
    raise ValueError("reranker backend must be one of: auto, lexical, cross-encoder")


def _lexical(query: str, candidates: list[RerankCandidate]) -> RerankResult:
    return RerankResult(
        scores={candidate.memory_id: lexical_rerank_score(query, candidate.text) for candidate in candidates},
        backend="lexical",
        model="lexical_overlap_v1",
    )


def _cross_encoder(query: str, candidates: list[RerankCandidate], model, model_name: str, max_chars: int) -> RerankResult:
    pairs = [(query, candidate.text[:max_chars]) for candidate in candidates]
    raw_scores = model.predict(pairs)
    values = [float(score) for score in raw_scores]
    normalized = _normalize(values)
    return RerankResult(
        scores={candidate.memory_id: score for candidate, score in zip(candidates, normalized)},
        backend="cross-encoder",
        model=model_name,
    )


def _normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    low = min(values)
    high = max(values)
    if high == low:
        return [1.0 for _ in values]
    return [(value - low) / (high - low) for value in values]
