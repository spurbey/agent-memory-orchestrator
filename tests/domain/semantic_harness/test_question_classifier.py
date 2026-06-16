from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness.query_modes import classify_context_question
from agent_memory_orchestrator.domain.semantic_harness.query_modes import classify_context_questions


def test_classifies_single_type_questions() -> None:
    assert classify_context_question("what invariant does this function maintain?").types == ("invariant",)
    assert classify_context_question("what tests validate this behavior?").types == ("validation",)
    assert classify_context_question("what calls this function?").types == ("usage",)


def test_classifies_multi_type_questions() -> None:
    result = classify_context_question("why does this exist and what will I break if I change it?")

    assert result.types == ("risk", "history")
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


def test_unknown_and_broad_questions_request_clarification() -> None:
    unknown = classify_context_question("please help")
    broad = classify_context_question("give me everything about this file")

    assert unknown.types == ("unknown",)
    assert unknown.status == "clarification_needed"
    assert broad.types == ("unknown",)
    assert broad.status == "clarification_needed"
    assert "too_broad" in broad.reason_codes


def test_batch_classification_preserves_order() -> None:
    results = classify_context_questions(
        (
            "what is this file responsible for?",
            "what should I avoid changing?",
        )
    )

    assert [result.types for result in results] == [("semantic_role",), ("risk",)]
