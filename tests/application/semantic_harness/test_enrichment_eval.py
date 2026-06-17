from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import BaselineLaneOutcome
from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessLaneOutcome
from agent_memory_orchestrator.application.services.semantic_harness import score_product_gate
from agent_memory_orchestrator.application.services.semantic_harness import score_used_answer


def test_used_answer_requires_fact_content_to_change_behavior() -> None:
    assert score_used_answer(
        SemanticHarnessLaneOutcome(
            returned_non_derivable_reason=True,
            graph_grounded_source_refs=True,
            plan_changed_with_fact_content=True,
        )
    )
    assert not score_used_answer(
        SemanticHarnessLaneOutcome(
            returned_non_derivable_reason=True,
            graph_grounded_source_refs=True,
            generic_amo_mention_only=True,
        )
    )
    assert not score_used_answer(
        SemanticHarnessLaneOutcome(
            returned_non_derivable_reason=True,
            graph_grounded_source_refs=True,
            plan_changed_with_fact_content=True,
            independent_reasoning_only=True,
        )
    )


def test_product_gate_passes_only_when_baseline_fails_and_semantic_answer_is_used() -> None:
    result = score_product_gate(
        baseline=BaselineLaneOutcome(guessed_or_failed=True),
        structural_recovered_reason=False,
        semantic=SemanticHarnessLaneOutcome(
            returned_non_derivable_reason=True,
            graph_grounded_source_refs=True,
            avoided_baseline_wrong_edit=True,
        ),
    )

    assert result.passed
    assert result.fixture_certification == "certified_non_derivable"
    assert result.used_answer


def test_product_gate_reclassifies_fixture_when_baseline_recovers_reason() -> None:
    result = score_product_gate(
        baseline=BaselineLaneOutcome(recovered_reason=True),
        structural_recovered_reason=False,
        semantic=SemanticHarnessLaneOutcome(
            returned_non_derivable_reason=True,
            graph_grounded_source_refs=True,
            avoided_baseline_wrong_edit=True,
        ),
    )

    assert not result.passed
    assert result.fixture_certification == "derivable_or_tedious"
    assert "derivable_or_tedious" in result.failure_reasons


def test_product_gate_fails_when_semantic_answer_is_not_used() -> None:
    result = score_product_gate(
        baseline=BaselineLaneOutcome(chose_wrong_edit=True),
        structural_recovered_reason=False,
        semantic=SemanticHarnessLaneOutcome(
            returned_non_derivable_reason=True,
            graph_grounded_source_refs=True,
            proceeded_same_as_baseline=True,
        ),
    )

    assert not result.passed
    assert "agent_did_not_use_answer" in result.failure_reasons
