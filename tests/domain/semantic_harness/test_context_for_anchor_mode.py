from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_context_for_anchor


def test_context_for_anchor_requires_a_question() -> None:
    graph = _graph()

    result = answer_context_for_anchor(graph, files=("src/snapshots.py",), questions=())

    assert result.status == "clarification_needed"
    assert result.answers == ()
    assert result.warnings == ("question_required",)


def test_context_for_anchor_answers_semantic_role_from_summary() -> None:
    graph = _graph()

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what is this file responsible for?",),
    )

    assert result.status == "ready"
    assert result.answers[0].question_type == "semantic_role"
    assert "structural graph snapshot identity" in result.answers[0].answer
    assert result.action_relevant_links == ()
    assert result.question_classifications[0].types == ("semantic_role",)


def test_context_for_anchor_returns_partial_when_invariant_is_missing() -> None:
    graph = _graph(include_invariant=False)

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what invariant does this function maintain?",),
    )

    assert result.status == "partial_structural"
    assert result.answers[0].question_type == "invariant"
    assert "No reviewed invariant" in result.answers[0].answer
    assert "structural_only:no_reviewed_semantic_context" in result.warnings


def test_context_for_anchor_returns_action_relevant_links_only_for_requested_usage() -> None:
    graph = _graph()

    role_result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what is this file responsible for?",),
    )
    usage_result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what calls this?",),
    )

    assert role_result.action_relevant_links == ()
    assert usage_result.action_relevant_links
    assert usage_result.action_relevant_links[0].kind == "CALLS"


def test_context_for_anchor_recommends_deeper_relationship_mode() -> None:
    graph = _graph()

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("does this relate to graph_store.py?",),
    )

    assert result.status == "partial_coverage"
    assert result.recommended_next_mode == "relationship_between_anchors"
    assert result.answers == ()


def _graph(*, include_invariant: bool = True) -> StructuralHarnessGraph:
    file_metadata = {"path": "src/snapshots.py"}
    if include_invariant:
        file_metadata["invariant"] = "Duplicate raw observations must not change structural identity."
    file_node = HarnessNode(
        id="file:repo:test:src/snapshots.py",
        kind="File",
        label="src/snapshots.py",
        repo_id="repo:test",
        summary="Owns structural graph snapshot identity and edge-count semantics.",
        metadata=file_metadata,
    )
    caller = HarnessNode(
        id="symbol:repo:test:runtime.bootstrap:function",
        kind="Symbol",
        label="runtime.bootstrap",
        repo_id="repo:test",
        metadata={"path": "src/runtime.py"},
    )
    test_node = HarnessNode(
        id="file:repo:test:tests/test_snapshots.py",
        kind="File",
        label="tests/test_snapshots.py",
        repo_id="repo:test",
        metadata={"path": "tests/test_snapshots.py"},
    )
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(file_node, caller, test_node),
        edges=(
            HarnessEdge(source_id=caller.id, target_id=file_node.id, kind="CALLS", confidence=0.8),
            HarnessEdge(source_id=file_node.id, target_id=test_node.id, kind="VALIDATED_BY", confidence=0.9),
        ),
    )
