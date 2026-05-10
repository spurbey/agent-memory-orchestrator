from __future__ import annotations

import re
from dataclasses import dataclass


RRF_K = 60


@dataclass(slots=True, frozen=True)
class QueryUnderstanding:
    intent: str
    include_historical: bool
    pools: dict[str, int]
    entities: list[str]


def understand_query(query: str, limit: int = 10) -> QueryUnderstanding:
    lowered = query.lower()
    entities = _extract_query_entities(query)
    include_historical = any(w in lowered for w in ("historical", "history", "previous", "old", "superseded", "versions"))

    if entities or re.search(r"\b[a-f0-9]{7,40}\b", lowered):
        intent = "exact"
    elif any(w in lowered for w in ("why", "reason", "caused", "rationale")):
        intent = "causal"
    elif any(w in lowered for w in ("changed", "before", "after", "when", "timeline", "versions")):
        intent = "historical"
        include_historical = True
    elif any(w in lowered for w in ("similar", "related", "concept", "idea")):
        intent = "semantic"
    else:
        intent = "mixed"

    pools = _pools_for_intent(intent, limit)
    return QueryUnderstanding(intent=intent, include_historical=include_historical, pools=pools, entities=entities)


def reciprocal_rank_fusion(rankings: dict[str, list[tuple[str, float]]], k: int = RRF_K) -> dict[str, dict]:
    fused: dict[str, dict] = {}
    for source, ranked_items in rankings.items():
        for idx, (memory_id, raw_score) in enumerate(ranked_items, start=1):
            item = fused.setdefault(
                memory_id,
                {"memory_id": memory_id, "rrf_score": 0.0, "sources": {}, "raw_scores": {}},
            )
            contribution = 1.0 / (k + idx)
            item["rrf_score"] += contribution
            item["sources"][source] = idx
            item["raw_scores"][source] = raw_score
    return fused


def lexical_rerank_score(query: str, text: str) -> float:
    query_terms = {term for term in _terms(query) if len(term) >= 3}
    if not query_terms:
        return 0.0
    text_terms = set(_terms(text))
    overlap = len(query_terms & text_terms) / len(query_terms)
    phrase_bonus = 0.15 if query.lower() in text.lower() else 0.0
    return min(1.0, overlap + phrase_bonus)


def _pools_for_intent(intent: str, limit: int) -> dict[str, int]:
    base = max(limit * 4, 20)
    if intent == "exact":
        return {"bm25": base * 2, "vector": base, "kg": base}
    if intent == "causal":
        return {"bm25": base, "vector": base, "kg": base * 2}
    if intent == "semantic":
        return {"bm25": base, "vector": base * 2, "kg": base}
    if intent == "historical":
        return {"bm25": base, "vector": base, "kg": base * 2}
    return {"bm25": base, "vector": base, "kg": base}


def _extract_query_entities(query: str) -> list[str]:
    path_re = re.compile(r"[\w./\\-]+\.(?:py|js|ts|tsx|jsx|go|rs|java|kt|swift|dart|md|json|toml|yaml|yml)")
    tick_re = re.compile(r"`([^`]{2,})`")
    entities = path_re.findall(query) + tick_re.findall(query)
    seen: set[str] = set()
    unique: list[str] = []
    for entity in entities:
        key = entity.lower()
        if key not in seen:
            seen.add(key)
            unique.append(entity)
    return unique


def _terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9_./-]+", text.lower())
