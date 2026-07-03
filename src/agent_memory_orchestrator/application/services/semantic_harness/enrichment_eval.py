from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class BaselineLaneOutcome:
    recovered_reason: bool = False
    chose_wrong_edit: bool = False
    guessed_or_failed: bool = False
    tool_call_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "recovered_reason": self.recovered_reason,
            "chose_wrong_edit": self.chose_wrong_edit,
            "guessed_or_failed": self.guessed_or_failed,
            "tool_call_count": self.tool_call_count,
        }


@dataclass(slots=True, frozen=True)
class SemanticHarnessLaneOutcome:
    returned_non_derivable_reason: bool
    graph_grounded_source_refs: bool
    plan_changed_with_fact_content: bool = False
    avoided_baseline_wrong_edit: bool = False
    skipped_baseline_fact_discovery: bool = False
    proceeded_same_as_baseline: bool = False
    independent_reasoning_only: bool = False
    generic_amo_mention_only: bool = False
    still_wrong_or_blind: bool = False

    @property
    def used_answer(self) -> bool:
        return score_used_answer(self)

    def as_dict(self) -> dict[str, Any]:
        return {
            "returned_non_derivable_reason": self.returned_non_derivable_reason,
            "graph_grounded_source_refs": self.graph_grounded_source_refs,
            "plan_changed_with_fact_content": self.plan_changed_with_fact_content,
            "avoided_baseline_wrong_edit": self.avoided_baseline_wrong_edit,
            "skipped_baseline_fact_discovery": self.skipped_baseline_fact_discovery,
            "proceeded_same_as_baseline": self.proceeded_same_as_baseline,
            "independent_reasoning_only": self.independent_reasoning_only,
            "generic_amo_mention_only": self.generic_amo_mention_only,
            "still_wrong_or_blind": self.still_wrong_or_blind,
            "used_answer": self.used_answer,
        }


@dataclass(slots=True, frozen=True)
class ProductGateResult:
    passed: bool
    fixture_certification: str
    used_answer: bool
    failure_reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "fixture_certification": self.fixture_certification,
            "used_answer": self.used_answer,
            "failure_reasons": list(self.failure_reasons),
        }


def score_used_answer(outcome: SemanticHarnessLaneOutcome) -> bool:
    positive = (
        outcome.plan_changed_with_fact_content
        or outcome.avoided_baseline_wrong_edit
        or outcome.skipped_baseline_fact_discovery
    )
    negative = (
        outcome.proceeded_same_as_baseline
        or outcome.independent_reasoning_only
        or outcome.generic_amo_mention_only
        or outcome.still_wrong_or_blind
    )
    return positive and not negative


def score_product_gate(
    *,
    baseline: BaselineLaneOutcome,
    structural_recovered_reason: bool,
    semantic: SemanticHarnessLaneOutcome,
) -> ProductGateResult:
    certification = _fixture_certification(baseline)
    failures: list[str] = []
    if certification != "certified_non_derivable":
        failures.append(certification)
    if structural_recovered_reason:
        failures.append("structural_harness_recovered_reason")
    if not semantic.returned_non_derivable_reason:
        failures.append("semantic_harness_missing_non_derivable_reason")
    if not semantic.graph_grounded_source_refs:
        failures.append("semantic_harness_missing_graph_grounded_source_refs")
    if not semantic.used_answer:
        failures.append("agent_did_not_use_answer")
    return ProductGateResult(
        passed=not failures,
        fixture_certification=certification,
        used_answer=semantic.used_answer,
        failure_reasons=tuple(failures),
    )


def _fixture_certification(baseline: BaselineLaneOutcome) -> str:
    if baseline.recovered_reason:
        return "derivable_or_tedious"
    if baseline.chose_wrong_edit or baseline.guessed_or_failed:
        return "certified_non_derivable"
    return "fixture_missing_non_derivable"


__all__ = [
    "BaselineLaneOutcome",
    "ProductGateResult",
    "SemanticHarnessLaneOutcome",
    "score_product_gate",
    "score_used_answer",
]
