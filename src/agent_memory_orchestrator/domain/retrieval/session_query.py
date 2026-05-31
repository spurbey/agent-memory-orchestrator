from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol


SESSION_QUERY_STOPWORDS = {
    "about",
    "after",
    "and",
    "are",
    "code",
    "did",
    "for",
    "from",
    "how",
    "into",
    "the",
    "this",
    "was",
    "what",
    "when",
    "where",
    "which",
    "why",
    "with",
}


class SessionGraphSearchStore(Protocol):
    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        ...

    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        ...


@dataclass(slots=True, frozen=True)
class SessionGraphHit:
    node: dict[str, Any]
    score: float
    reasons: tuple[str, ...]
    neighbors: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "score": self.score,
            "reasons": list(self.reasons),
            "neighbors": list(self.neighbors),
        }


def query_session_graph(
    store: SessionGraphSearchStore,
    query: str,
    *,
    session_id: str,
    kinds: list[str] | None = None,
    limit: int = 10,
    expand_neighbors: int = 8,
    candidate_limit: int = 10000,
) -> tuple[SessionGraphHit, ...]:
    candidates = store.list_nodes(limit=candidate_limit, kinds=kinds, session_id=session_id)
    terms = _terms(query)
    phrase = _normalized_text(query)
    scored: list[tuple[dict[str, Any], float, tuple[str, ...]]] = []
    for node in candidates:
        score, reasons = _score_node(node, terms=terms, phrase=phrase)
        if score <= 0:
            continue
        scored.append((node, round(score, 6), tuple(reasons)))
    scored.sort(key=lambda hit: (hit[1], _kind_priority(str(hit[0].get("kind") or ""))), reverse=True)

    hits: list[SessionGraphHit] = []
    for node, score, reasons in scored[: max(1, int(limit))]:
        neighbors = tuple(store.neighbors(str(node.get("id") or ""), limit=expand_neighbors)) if expand_neighbors else ()
        hits.append(SessionGraphHit(node=node, score=score, reasons=reasons, neighbors=neighbors))
    return tuple(hits)


def _score_node(node: dict[str, Any], *, terms: set[str], phrase: str) -> tuple[float, list[str]]:
    text = _node_text(node)
    node_terms = _terms(text)
    reasons: list[str] = []
    score = 0.0
    if phrase and phrase in _normalized_text(text):
        score += 1.5
        reasons.append("phrase_match")
    if terms and node_terms:
        overlap = terms.intersection(node_terms)
        if overlap:
            ratio = len(overlap) / max(1, len(terms))
            score += ratio
            reasons.append("term_overlap:" + ",".join(sorted(overlap)[:8]))
    kind = str(node.get("kind") or "")
    if kind == "WorkChange":
        score += 0.35
        reasons.append("work_change")
    if kind == "CodeNode":
        score += 0.15
        reasons.append("code_node")
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        ast_status = str(metadata.get("ast_status") or node.get("ast_status") or "")
        if ast_status == "parsed":
            score += 0.10
            reasons.append("parsed_code")
    return score, reasons


def _node_text(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    try:
        metadata_text = json.dumps(metadata, sort_keys=True)
    except TypeError:
        metadata_text = str(metadata)
    return " ".join(
        str(part or "")
        for part in (
            node.get("id"),
            node.get("kind"),
            node.get("label"),
            node.get("summary"),
            node.get("commit_id"),
            node.get("evidence_id"),
            metadata_text,
        )
    )


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in re.split(r"[^a-zA-Z0-9]+", str(text).lower()):
        if len(token) <= 2 or token in SESSION_QUERY_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        terms.add(token)
    return terms


def _normalized_text(text: str) -> str:
    return " ".join(re.findall(r"[a-zA-Z0-9]+", str(text).lower()))


def _kind_priority(kind: str) -> int:
    if kind == "WorkChange":
        return 3
    if kind == "CodeNode":
        return 2
    return 1
