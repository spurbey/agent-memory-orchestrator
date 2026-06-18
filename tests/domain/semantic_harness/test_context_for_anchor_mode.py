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
    assert result.answers[0].fact_type == "semantic_role"
    assert result.answers[0].derivability == "derivable_from_current_code"
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


def test_context_for_anchor_uses_typed_fact_derivability_for_invariant() -> None:
    graph = _graph(
        semantic_facts=(
            {
                "fact_type": "invariant_or_contract",
                "text": "Raw graph edges are observation-level; SQLite stores unique logical edge keys.",
                "review_status": "accepted",
                "derivability": "derivable_from_current_code",
                "discovery_cost": "medium",
                "confidence": 0.86,
                "source_refs": [{"node_id": "file:repo:test:src/snapshots.py", "path": "src/snapshots.py"}],
            },
        ),
        include_invariant=False,
    )

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what invariant does this maintain?",),
    )

    assert result.status == "ready"
    assert result.answers[0].answer.startswith("Raw graph edges")
    assert result.answers[0].fact_type == "invariant_or_contract"
    assert result.answers[0].derivability == "derivable_from_current_code"
    assert result.answers[0].review_status == "accepted"
    assert result.answers[0].discovery_cost == "medium"


def test_context_for_anchor_prefers_non_derivable_risk_fact() -> None:
    graph = _graph(
        semantic_facts=(
            {
                "fact_type": "risk_or_impact",
                "text": "Changing this looks local in current code.",
                "review_status": "accepted",
                "derivability": "derivable_from_current_code",
                "confidence": 0.91,
            },
            {
                "fact_type": "risk_or_impact",
                "text": "Do not merge raw and unique edge counts; a prior diagnostic proved duplicates are expected.",
                "review_status": "accepted",
                "derivability": "requires_git_history",
                "discovery_cost": "high",
                "confidence": 0.82,
            },
        ),
        include_invariant=False,
    )

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what will break if I change this?",),
    )

    assert result.status == "ready"
    assert "Do not merge raw and unique edge counts" in result.answers[0].answer
    assert result.answers[0].derivability == "requires_git_history"
    assert result.answers[0].discovery_cost == "high"


def test_context_for_anchor_answers_local_rationale_fact_while_recommending_history_mode() -> None:
    graph = _graph(
        semantic_facts=(
            {
                "fact_type": "implementation_rationale",
                "text": "This keeps raw and unique counts separate because a prior diagnostic proved duplicate import observations are expected.",
                "review_status": "accepted",
                "derivability": "requires_agent_session_history",
                "discovery_cost": "hidden",
                "confidence": 0.84,
            },
        ),
        include_invariant=False,
    )

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("why does this exist?",),
    )

    assert result.status == "ready"
    assert result.recommended_next_mode == "history_for_anchor"
    assert "prior diagnostic" in result.answers[0].answer
    assert result.answers[0].fact_type == "implementation_rationale"
    assert result.answers[0].derivability == "requires_agent_session_history"


def test_context_for_anchor_answers_validation_expectation_fact() -> None:
    graph = _graph(
        semantic_facts=(
            {
                "fact_type": "validation_expectation",
                "text": "Pending checkpoint review must write artifacts before any accepted-only graph attach runs.",
                "review_status": "accepted",
                "derivability": "requires_agent_session_history",
                "confidence": 0.88,
            },
        ),
        include_invariant=False,
    )

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what validation exists for this path?",),
    )

    assert result.status == "ready"
    assert result.answers[0].question_type == "validation"
    assert "Pending checkpoint review" in result.answers[0].answer
    assert result.answers[0].fact_type == "validation_expectation"
    assert result.action_relevant_links[0].kind == "VALIDATED_BY"


def test_context_for_anchor_does_not_treat_boilerplate_summary_as_ready_semantic_role() -> None:
    graph = _graph(summary="from __future__ import annotations")

    result = answer_context_for_anchor(
        graph,
        files=("src/snapshots.py",),
        questions=("what is this file responsible for?",),
    )

    assert result.status == "partial_structural"
    assert "No reviewed semantic role fact" in result.answers[0].answer


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


def _graph(
    *,
    include_invariant: bool = True,
    semantic_facts: tuple[dict[str, object], ...] = (),
    summary: str = "Owns structural graph snapshot identity and edge-count semantics.",
) -> StructuralHarnessGraph:
    file_metadata = {"path": "src/snapshots.py"}
    if include_invariant:
        file_metadata["invariant"] = "Duplicate raw observations must not change structural identity."
    if semantic_facts:
        file_metadata["semantic_facts"] = semantic_facts
    file_node = HarnessNode(
        id="file:repo:test:src/snapshots.py",
        kind="File",
        label="src/snapshots.py",
        repo_id="repo:test",
        summary=summary,
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
