from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness.query_modes import classify_context_question
from agent_memory_orchestrator.domain.semantic_harness.query_modes import classify_context_questions


def test_classifies_single_type_questions() -> None:
    assert classify_context_question("what invariant does this function maintain?").types == ("invariant",)
    assert classify_context_question("what tests validate this behavior?").types == ("validation",)
    assert classify_context_question("what validation exists for this path?").types == ("validation",)
    assert classify_context_question("what calls this function?").types == ("usage",)


def test_classifies_multi_type_questions() -> None:
    result = classify_context_question("why does this exist and what will I break if I change it?")

    assert result.types == ("history", "risk")
    assert result.status == "ready"
    assert result.recommended_mode == "context_for_anchor"
    assert "break_or_impact" in result.reason_codes
    assert "why_exist_or_changed" in result.reason_codes


def test_recommends_deeper_mode_for_relationship_questions() -> None:
    result = classify_context_question("does this relate to graph_store.py?")

    assert result.types == ("local_relation",)
    assert result.status == "recommend_deeper_mode"
    assert result.recommended_mode == "relationship_between_anchors"


def test_recommends_history_mode_for_pure_history_questions() -> None:
    result = classify_context_question("when was this behavior introduced?")

    assert result.types == ("history",)
    assert result.status == "recommend_deeper_mode"
    assert result.recommended_mode == "history_for_anchor"


def test_generic_use_word_does_not_trigger_usage_route() -> None:
    result = classify_context_question("why does this path use pending review before graph mutation?")

    assert result.types == ("history",)


def test_before_editing_does_not_trigger_history_route() -> None:
    result = classify_context_question("what risk or impact should I know before editing?")

    assert result.types == ("risk",)


def test_classifies_operational_enforcement_phrasing() -> None:
    rejects = classify_context_question("what rejects intermediate hypotheses?")
    enforces = classify_context_question("what enforces accepted-only attach?")
    filters = classify_context_question("what filters unverified docs?")
    prevents = classify_context_question("what prevents stale facts leaking?")
    guards = classify_context_question("what guards this behavior?")

    assert rejects.types == ("invariant", "validation", "history")
    assert enforces.types == ("invariant", "validation")
    assert filters.types == ("invariant", "history")
    assert prevents.types == ("invariant", "risk")
    assert guards.types == ("invariant", "risk")
    assert "rejection_term" in rejects.reason_codes
    assert "enforcement_term" in enforces.reason_codes
    assert "filtering_term" in filters.reason_codes
    assert "prevention_term" in prevents.reason_codes
    assert "guard_term" in guards.reason_codes


def test_classifies_general_decision_rationale_phrasing() -> None:
    decision_risk = classify_context_question("is this choice intentional and what would break if changed?")
    kept = classify_context_question("why was this kept instead of the alternative?")
    rejected = classify_context_question("what rejected approach explains this?")
    validation = classify_context_question("what validates this behavior?")
    usage = classify_context_question("what calls this?")

    assert decision_risk.types == ("history", "risk")
    assert "decision_or_tradeoff" in decision_risk.reason_codes
    assert "break_or_impact" in decision_risk.reason_codes
    assert kept.types == ("history",)
    assert "decision_or_tradeoff" in kept.reason_codes
    assert rejected.types == ("history",)
    assert validation.types == ("validation",)
    assert usage.types == ("usage",)


def test_unknown_and_broad_questions_request_clarification() -> None:
    unknown = classify_context_question("please help")
    broad = classify_context_question("give me everything about this file")
    vague = classify_context_question("what should I know here?")

    assert unknown.types == ("unknown",)
    assert unknown.status == "clarification_needed"
    assert broad.types == ("unknown",)
    assert broad.status == "clarification_needed"
    assert "too_broad" in broad.reason_codes
    assert vague.types == ("unknown",)
    assert vague.status == "clarification_needed"


def test_batch_classification_preserves_order() -> None:
    results = classify_context_questions(
        (
            "what is this file responsible for?",
            "what should I avoid changing?",
        )
    )

    assert [result.types for result in results] == [("semantic_role",), ("risk",)]
