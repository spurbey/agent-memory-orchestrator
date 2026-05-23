from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class AnswerQuality:
    answer_grade: bool
    confidence: float
    reasons: tuple[str, ...]
    gaps: tuple[str, ...]
    citation_count: int
    top_hit_score: float
    vector_ok: bool
    intent_match: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "answer_grade": self.answer_grade,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "gaps": list(self.gaps),
            "citation_count": self.citation_count,
            "top_hit_score": self.top_hit_score,
            "vector_ok": self.vector_ok,
            "intent_match": self.intent_match,
        }


class AnswerQualityEvaluator:
    def evaluate(self, retrieval_result: dict[str, Any], *, query: str, min_confidence: float = 0.72) -> AnswerQuality:
        retrieval = retrieval_result.get("retrieval") if isinstance(retrieval_result.get("retrieval"), dict) else {}
        answer = retrieval_result.get("answer") if isinstance(retrieval_result.get("answer"), dict) else {}
        hits = retrieval.get("hits") if isinstance(retrieval.get("hits"), list) else []
        citations = answer.get("citations") if isinstance(answer.get("citations"), list) else []
        top = hits[0] if hits and isinstance(hits[0], dict) else {}
        doc = top.get("document") if isinstance(top.get("document"), dict) else {}
        graph_node = top.get("graph_node") if isinstance(top.get("graph_node"), dict) else {}
        top_score = _float(top.get("score"))
        vector_status = str(retrieval.get("vector_status") or "not_requested")
        vector_ok = vector_status in {"not_requested", "faiss:completed", "completed", "skipped:no_vectors", "skipped"}
        node_kind = str(doc.get("node_kind") or graph_node.get("kind") or "").lower()
        doc_type = str(doc.get("doc_type") or "").lower()
        has_answer_node = any(term in f"{node_kind} {doc_type}" for term in ("reason", "decision", "work", "fix", "problem"))
        has_citation = bool(citations)
        has_shared_anchor = any(_citation_has_anchor(citation) for citation in citations if isinstance(citation, dict))
        intent_match = _query_overlap(query, " ".join([str(doc.get("title") or ""), str(doc.get("body") or "")])) >= 0.18

        reasons: list[str] = []
        gaps: list[str] = []
        score = 0.10
        if hits:
            score += 0.15
            reasons.append("retrieval returned hits")
        else:
            gaps.append("no retrieval hits")
        if has_answer_node:
            score += 0.20
            reasons.append("top hit is answer-grade node")
        else:
            gaps.append("top hit is not clearly answer-grade")
        if has_citation:
            score += 0.20
            reasons.append("answer has citations")
        else:
            gaps.append("answer has no citations")
        if has_shared_anchor:
            score += 0.10
            reasons.append("citations include shared anchors")
        if vector_ok:
            score += 0.10
        else:
            gaps.append(f"vector retrieval not ready: {vector_status}")
        if intent_match:
            score += 0.15
            reasons.append("query overlaps top hit")
        else:
            gaps.append("query does not strongly overlap top hit")
        if top_score > 0:
            score += min(0.10, top_score / 10.0)

        confidence = max(0.0, min(1.0, score))
        answer_grade = bool(confidence >= min_confidence and has_citation and has_answer_node and intent_match)
        return AnswerQuality(
            answer_grade=answer_grade,
            confidence=confidence,
            reasons=tuple(reasons),
            gaps=tuple(gaps),
            citation_count=len(citations),
            top_hit_score=top_score,
            vector_ok=vector_ok,
            intent_match=intent_match,
        )


def _citation_has_anchor(citation: dict[str, Any]) -> bool:
    return bool(
        citation.get("commit_sha")
        or citation.get("commit_shas")
        or citation.get("code_nodes")
        or citation.get("code_node_ids")
        or citation.get("packet_id")
        or citation.get("packet_ids")
    )


def _query_overlap(query: str, text: str) -> float:
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    text_terms = _terms(text)
    if not text_terms:
        return 0.0
    return len(query_terms & text_terms) / max(1, len(query_terms))


def _terms(text: str) -> set[str]:
    stop = {"a", "an", "and", "are", "did", "do", "does", "for", "how", "in", "is", "of", "on", "the", "to", "was", "what", "why"}
    return {part.strip(".,:;!?()[]{}\"'").lower() for part in str(text).split() if len(part.strip()) > 2} - stop


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
