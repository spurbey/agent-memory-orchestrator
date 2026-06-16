from __future__ import annotations

from dataclasses import dataclass

from .question_classifier import QuestionClassification


@dataclass(slots=True, frozen=True)
class ContextAnswer:
    question: str
    question_type: str
    answer: str
    confidence: float
    evidence: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "question_type": self.question_type,
            "answer": self.answer,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
        }


@dataclass(slots=True, frozen=True)
class ActionRelevantLink:
    kind: str
    target_node_id: str
    target_label: str
    why: str
    confidence: float

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "target_node_id": self.target_node_id,
            "target_label": self.target_label,
            "why": self.why,
            "confidence": self.confidence,
        }


@dataclass(slots=True, frozen=True)
class ContextForAnchorResult:
    status: str
    answers: tuple[ContextAnswer, ...]
    invariants: tuple[str, ...]
    action_relevant_links: tuple[ActionRelevantLink, ...]
    recommended_next_mode: str | None
    question_classifications: tuple[QuestionClassification, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "answers": [answer.as_dict() for answer in self.answers],
            "invariants": list(self.invariants),
            "action_relevant_links": [link.as_dict() for link in self.action_relevant_links],
            "recommended_next_mode": self.recommended_next_mode,
            "question_classifications": [classification.as_dict() for classification in self.question_classifications],
            "warnings": list(self.warnings),
        }


__all__ = [
    "ActionRelevantLink",
    "ContextAnswer",
    "ContextForAnchorResult",
]
