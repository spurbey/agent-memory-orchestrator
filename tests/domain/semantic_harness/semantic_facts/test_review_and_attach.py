from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import ANCHOR_LOCAL_SCOPE
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import RELATIONSHIP_SCOPE
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REQUIRES_AGENT_SESSION_HISTORY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REQUIRES_GIT_HISTORY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REVIEW_ACCEPTED
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REVIEW_REJECTED
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REVIEW_REVIEW_ONLY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_AGENT_SESSION
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_DOCS
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_HUMAN_COMMIT
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SPAN_DOC_CLAIM
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SPAN_INTERMEDIATE_HYPOTHESIS
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SPAN_VALIDATED_COMMITTED
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import UNVERIFIED
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SemanticFactProposal
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SemanticFactSourceRef
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import attach_reviewed_facts_to_store
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import review_semantic_fact_proposals
from agent_memory_orchestrator.domain.semantic_harness.store import InMemoryHarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_context_for_anchor


def test_review_accepts_relationship_fact_with_multiple_anchors() -> None:
    graph = _graph()
    proposal = SemanticFactProposal(
        fact_type="relationship_reason",
        text="Snapshot identity and SQLite persistence must keep raw observations separate from unique stored edges.",
        anchor_node_ids=("file:repo:test:src/snapshots.py", "file:repo:test:src/graph_store.py"),
        source_refs=(_source_ref("commit:abc123"),),
        derivability=REQUIRES_GIT_HISTORY,
        source_kind=SOURCE_HUMAN_COMMIT,
        source_span="commit_message",
        fact_scope=RELATIONSHIP_SCOPE,
        confidence=0.83,
    )

    review = review_semantic_fact_proposals(graph=graph, proposals=(proposal,))

    assert len(review.accepted_facts) == 1
    fact = review.accepted_facts[0]
    assert fact.review_status == REVIEW_ACCEPTED
    assert fact.fact_scope == RELATIONSHIP_SCOPE
    assert fact.trust_tier == 2


def test_review_downgrades_unverified_doc_fact() -> None:
    graph = _graph()
    proposal = SemanticFactProposal(
        fact_type="invariant_or_contract",
        text="Docs claim raw and unique edge counts intentionally differ.",
        anchor_node_ids=("file:repo:test:src/snapshots.py",),
        source_refs=(_source_ref("doc:edge-count"),),
        derivability="derivable_from_docs",
        source_kind=SOURCE_DOCS,
        source_span=SPAN_DOC_CLAIM,
        fact_scope=ANCHOR_LOCAL_SCOPE,
        verification_status=UNVERIFIED,
        confidence=0.9,
    )

    review = review_semantic_fact_proposals(graph=graph, proposals=(proposal,))

    assert len(review.review_only_facts) == 1
    assert review.review_only_facts[0].review_status == REVIEW_REVIEW_ONLY
    assert any(diagnostic["reason"] == "doc_fact_unverified_current" for diagnostic in review.diagnostics)


def test_review_rejects_agent_intermediate_hypothesis() -> None:
    graph = _graph()
    proposal = SemanticFactProposal(
        fact_type="implementation_rationale",
        text="The agent guessed this was caused by a database constraint before validation.",
        anchor_node_ids=("file:repo:test:src/snapshots.py",),
        source_refs=(_source_ref("session:hypothesis"),),
        derivability=REQUIRES_AGENT_SESSION_HISTORY,
        source_kind=SOURCE_AGENT_SESSION,
        source_span=SPAN_INTERMEDIATE_HYPOTHESIS,
        fact_scope=ANCHOR_LOCAL_SCOPE,
        confidence=0.88,
    )

    review = review_semantic_fact_proposals(graph=graph, proposals=(proposal,))

    assert len(review.rejected_facts) == 1
    assert review.rejected_facts[0].review_status == REVIEW_REJECTED
    assert any(diagnostic["reason"] == "agent_session_unvalidated_span" for diagnostic in review.diagnostics)


def test_review_rejects_generic_fact_text() -> None:
    graph = _graph()
    proposal = SemanticFactProposal(
        fact_type="implementation_rationale",
        text="Updated the function.",
        anchor_node_ids=("file:repo:test:src/snapshots.py",),
        source_refs=(_source_ref("commit:abc123"),),
        derivability=REQUIRES_GIT_HISTORY,
        source_kind=SOURCE_HUMAN_COMMIT,
        fact_scope=ANCHOR_LOCAL_SCOPE,
        confidence=0.7,
    )

    review = review_semantic_fact_proposals(graph=graph, proposals=(proposal,))

    assert len(review.rejected_facts) == 1
    assert any(diagnostic["reason"] == "generic_fact_text" for diagnostic in review.diagnostics)


def test_attach_writes_accepted_facts_to_all_anchor_nodes() -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    proposal = SemanticFactProposal(
        fact_type="relationship_reason",
        text="The snapshot and persistence files jointly define raw-observation versus unique-edge semantics.",
        anchor_node_ids=("file:repo:test:src/snapshots.py", "file:repo:test:src/graph_store.py"),
        source_refs=(_source_ref("session:validated"),),
        derivability=REQUIRES_AGENT_SESSION_HISTORY,
        source_kind=SOURCE_AGENT_SESSION,
        source_span=SPAN_VALIDATED_COMMITTED,
        fact_scope=RELATIONSHIP_SCOPE,
        confidence=0.81,
    )
    review = review_semantic_fact_proposals(graph=graph, proposals=(proposal,))

    result = attach_reviewed_facts_to_store(store, facts=review.accepted_facts)

    assert result.attached_fact_ids == (review.accepted_facts[0].fact_id,)
    assert result.updated_node_ids == ("file:repo:test:src/graph_store.py", "file:repo:test:src/snapshots.py")
    for node_id in result.updated_node_ids:
        node = store.get_node(node_id)
        assert node is not None
        assert node.metadata["semantic_facts"][0]["review_status"] == REVIEW_ACCEPTED
        assert node.metadata["semantic_facts"][0]["fact_scope"] == RELATIONSHIP_SCOPE


def test_attached_fact_is_consumed_by_context_for_anchor() -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    proposal = SemanticFactProposal(
        fact_type="risk_or_impact",
        text="Do not collapse these counts; the validated session proved raw observations can duplicate logical edges.",
        anchor_node_ids=("file:repo:test:src/snapshots.py",),
        source_refs=(_source_ref("session:validated"),),
        derivability=REQUIRES_AGENT_SESSION_HISTORY,
        source_kind=SOURCE_AGENT_SESSION,
        source_span=SPAN_VALIDATED_COMMITTED,
        fact_scope=ANCHOR_LOCAL_SCOPE,
        confidence=0.84,
    )
    review = review_semantic_fact_proposals(graph=graph, proposals=(proposal,))
    attach_reviewed_facts_to_store(store, facts=review.accepted_facts)

    result = answer_context_for_anchor(
        store.to_graph(),
        files=("src/snapshots.py",),
        questions=("what will break if I change this?",),
    )

    assert result.status == "ready"
    assert result.answers[0].answer.startswith("Do not collapse")
    assert result.answers[0].source_kind == SOURCE_AGENT_SESSION
    assert result.answers[0].fact_scope == ANCHOR_LOCAL_SCOPE
    assert result.answers[0].trust_tier == 3


def test_context_prefers_higher_trust_fact_over_higher_confidence_session_fact() -> None:
    graph = _graph()
    store = InMemoryHarnessGraphStore.from_graph(graph)
    session_proposal = SemanticFactProposal(
        fact_type="risk_or_impact",
        text="The session noticed this may affect edge persistence.",
        anchor_node_ids=("file:repo:test:src/snapshots.py",),
        source_refs=(_source_ref("session:validated"),),
        derivability=REQUIRES_AGENT_SESSION_HISTORY,
        source_kind=SOURCE_AGENT_SESSION,
        source_span=SPAN_VALIDATED_COMMITTED,
        fact_scope=ANCHOR_LOCAL_SCOPE,
        confidence=0.99,
    )
    human_proposal = SemanticFactProposal(
        fact_type="risk_or_impact",
        text="The commit intentionally preserves raw edge observations separately from persisted unique edges.",
        anchor_node_ids=("file:repo:test:src/snapshots.py",),
        source_refs=(_source_ref("commit:abc123"),),
        derivability=REQUIRES_GIT_HISTORY,
        source_kind=SOURCE_HUMAN_COMMIT,
        source_span="commit_message",
        fact_scope=ANCHOR_LOCAL_SCOPE,
        confidence=0.8,
    )
    review = review_semantic_fact_proposals(graph=graph, proposals=(session_proposal, human_proposal))
    attach_reviewed_facts_to_store(store, facts=review.accepted_facts)

    result = answer_context_for_anchor(
        store.to_graph(),
        files=("src/snapshots.py",),
        questions=("what will break if I change this?",),
    )

    assert result.status == "ready"
    assert result.answers[0].answer.startswith("The commit intentionally")
    assert result.answers[0].trust_tier == 2


def _graph() -> StructuralHarnessGraph:
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(
                id="file:repo:test:src/snapshots.py",
                kind="File",
                label="src/snapshots.py",
                repo_id="repo:test",
                metadata={"path": "src/snapshots.py"},
            ),
            HarnessNode(
                id="file:repo:test:src/graph_store.py",
                kind="File",
                label="src/graph_store.py",
                repo_id="repo:test",
                metadata={"path": "src/graph_store.py"},
            ),
        ),
        edges=(),
    )


def _source_ref(ref_id: str) -> SemanticFactSourceRef:
    return SemanticFactSourceRef(ref_id=ref_id, ref_kind=ref_id.split(":", 1)[0])
