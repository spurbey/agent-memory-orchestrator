from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable


CONTEXT_QUESTION_TYPES = (
    "semantic_role",
    "invariant",
    "validation",
    "risk",
    "local_relation",
    "history",
    "usage",
    "unknown",
)

_OUTPUT_TYPE_ORDER = {
    question_type: index
    for index, question_type in enumerate(
        (
            "semantic_role",
            "invariant",
            "validation",
            "history",
            "risk",
            "local_relation",
            "usage",
            "unknown",
        )
    )
}


@dataclass(slots=True, frozen=True)
class QuestionClassification:
    question: str
    types: tuple[str, ...]
    confidence: float
    reason_codes: tuple[str, ...]
    status: str = "ready"
    recommended_mode: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "types": list(self.types),
            "confidence": self.confidence,
            "reason_codes": list(self.reason_codes),
            "status": self.status,
            "recommended_mode": self.recommended_mode,
        }


def classify_context_questions(questions: Iterable[str]) -> tuple[QuestionClassification, ...]:
    return tuple(classify_context_question(question) for question in questions)


def classify_context_question(question: str) -> QuestionClassification:
    raw = str(question or "").strip()
    text = _normalize(raw)
    if not text:
        return QuestionClassification(
            question=raw,
            types=("unknown",),
            confidence=0.0,
            reason_codes=("empty_question",),
            status="clarification_needed",
        )

    if _matches_any(text, _TOO_BROAD_PATTERNS):
        return QuestionClassification(
            question=raw,
            types=("unknown",),
            confidence=0.2,
            reason_codes=("too_broad",),
            status="clarification_needed",
        )

    matched: list[tuple[str, str]] = []
    for question_type, patterns in _TYPE_PATTERNS:
        if _matches_any(text, patterns):
            matched.append((question_type, _reason_code_for(question_type, text)))

    types = _ordered_types(_dedupe(question_type for question_type, _ in matched))
    reason_codes = _dedupe(reason_code for _, reason_code in matched)
    if not types:
        return QuestionClassification(
            question=raw,
            types=("unknown",),
            confidence=0.25,
            reason_codes=("no_route_match",),
            status="clarification_needed",
        )

    recommended_mode = _recommended_mode(types)
    status = "ready"
    if recommended_mode and recommended_mode != "context_for_anchor":
        status = "recommend_deeper_mode"

    return QuestionClassification(
        question=raw,
        types=types,
        confidence=_confidence_for(types, reason_codes),
        reason_codes=reason_codes,
        status=status,
        recommended_mode=recommended_mode,
    )


def _normalize(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    value = value.replace("_", " ")
    value = re.sub(r"[^a-zA-Z0-9\s?.-]+", " ", value)
    return re.sub(r"\s+", " ", value.lower()).strip()


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _ordered_types(types: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted(types, key=lambda question_type: _OUTPUT_TYPE_ORDER.get(question_type, 99)))


def _reason_code_for(question_type: str, text: str) -> str:
    if question_type == "history" and re.search(
        r"\b(intentional|choice|decision|decided|tradeoff|trade off|why not|instead of|kept|rejected approach|review suggestion)\b",
        text,
    ):
        return "decision_or_tradeoff"
    if re.search(r"\b(reject|rejects|rejected|rejecting)\b", text):
        return "rejection_term"
    if re.search(r"\b(enforce|enforces|enforced|enforcing)\b", text):
        return "enforcement_term"
    if re.search(r"\b(filter|filters|filtered|filtering)\b", text):
        return "filtering_term"
    if re.search(r"\b(guard|guards|guarded|guarding)\b", text):
        return "guard_term"
    if re.search(r"\b(prevent|prevents|prevented|preventing)\b", text):
        return "prevention_term"
    if question_type == "history" and re.search(r"\bwhy\b.*\b(exist|created|built|introduced|changed)\b", text):
        return "why_exist_or_changed"
    if question_type == "risk" and re.search(r"\b(break|affect|impact|risk)\b", text):
        return "break_or_impact"
    if question_type == "local_relation" and re.search(r"\b(relate|connect|link|between|relationship)\b", text):
        return "relationship_requested"
    if question_type == "usage" and re.search(r"\b(calls?|called by|uses?|used by|depends?|imports?)\b", text):
        return "usage_requested"
    return f"{question_type}_term"


def _recommended_mode(types: tuple[str, ...]) -> str:
    if "local_relation" in types:
        return "relationship_between_anchors"
    if "history" in types and len(types) == 1:
        return "history_for_anchor"
    return "context_for_anchor"


def _confidence_for(types: tuple[str, ...], reason_codes: tuple[str, ...]) -> float:
    if types == ("unknown",):
        return 0.25
    score = 0.62 + min(0.22, len(reason_codes) * 0.07)
    if len(types) > 1:
        score -= 0.04
    return round(min(0.92, max(0.35, score)), 2)


_TOO_BROAD_PATTERNS = (
    r"\btell me everything\b",
    r"\bgive me everything\b",
    r"\bexplain everything\b",
    r"\bfull context\b",
    r"\bwhat should i know here\b",
    r"\bprofile (this|the) (file|function|class|symbol)\b",
)


_TYPE_PATTERNS = (
    (
        "semantic_role",
        (
            r"\b(responsible for|responsibility|role|purpose)\b",
            r"\bwhat (is|does) (this|the)\b",
            r"\bwhat is .* about\b",
        ),
    ),
    (
        "invariant",
        (
            r"\b(invariant|guarantee|constraint|contract)\b",
            r"\bmust (stay|remain|hold|not)\b",
            r"\bshould (stay|remain|not change|avoid changing)\b",
            r"\b(reject|rejects|rejecting)\b",
            r"\b(enforce|enforces|enforced|enforcing)\b",
            r"\b(filter|filters|filtered|filtering)\b",
            r"\b(guard|guards|guarded|guarding)\b",
            r"\b(prevent|prevents|prevented|preventing)\b",
        ),
    ),
    (
        "validation",
        (
            r"\b(test|tests|tested|validate|validates|validated|validation|verify|verifies|verification)\b",
            r"\bcoverage\b",
            r"\b(reject|rejects|rejecting)\b",
            r"\b(enforce|enforces|enforced|enforcing)\b",
        ),
    ),
    (
        "risk",
        (
            r"\b(break|breaks|broken|risk|risky|impact|affect|affected|side effect|regression)\b",
            r"\bwhat (will|could|might) .* if i change\b",
            r"\bwhat should i avoid\b",
            r"\b(guard|guards|guarded|guarding)\b",
            r"\b(prevent|prevents|prevented|preventing)\b",
        ),
    ),
    (
        "local_relation",
        (
            r"\b(relate|relates|related|connect|connects|connected|link|links|linked|relationship)\b",
            r"\bbetween\b",
        ),
    ),
    (
        "history",
        (
            r"\bwhy\b",
            r"\bwhen\b",
            r"\b(changed|created|introduced|built|origin|history)\b",
            r"\b(intentional|choice|decision|decided|tradeoff|trade off)\b",
            r"\bwhy not\b",
            r"\binstead of\b",
            r"\bkept\b",
            r"\brejected (approach|option|alternative|suggestion)\b",
            r"\breview suggestion\b",
            r"\b(filter|filters|filtered|filtering)\b",
            r"\b(reject|rejects|rejected|rejecting)\b",
        ),
    ),
    (
        "usage",
        (
            r"\b(calls?|called by|used by|depends?|dependencies|imports?|imported by)\b",
            r"\bwhat uses\b",
            r"\bwho uses\b",
        ),
    ),
)


__all__ = [
    "CONTEXT_QUESTION_TYPES",
    "QuestionClassification",
    "classify_context_question",
    "classify_context_questions",
]
