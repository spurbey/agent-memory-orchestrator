from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_context_for_anchor
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import ANCHOR_LOCAL_SCOPE
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REQUIRES_GIT_HISTORY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REVIEW_REVIEW_ONLY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_AGENT_SESSION
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_HUMAN_COMMIT
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SPAN_COMMIT_MESSAGE
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SPAN_INTERMEDIATE_HYPOTHESIS
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import STALE_RISK
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import VERIFIED_AT_COMMIT
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SemanticFactProposal
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SemanticFactSourceRef
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import build_semantic_evidence_packet
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import mark_stale_facts_for_changed_anchors
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import parse_semantic_fact_proposals
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import review_semantic_fact_proposals


def test_packet_builder_emits_stable_source_refs() -> None:
    build = build_semantic_evidence_packet(
        _graph(),
        source_kind=SOURCE_HUMAN_COMMIT,
        source_span=SPAN_COMMIT_MESSAGE,
        source_id="abc123",
        anchor_node_ids=("file:repo:test:src/auth.py",),
        source_refs=(SemanticFactSourceRef(ref_id="commit:abc123", ref_kind="commit"),),
        payload={"message": "Preserve login None semantics."},
    )

    assert build.passed
    assert build.packet is not None
    assert build.packet.packet_id.startswith("semantic_packet:")
    assert build.packet.source_refs == ({"ref_id": "commit:abc123", "ref_kind": "commit"},)


def test_packet_builder_excludes_intermediate_agent_hypotheses() -> None:
    build = build_semantic_evidence_packet(
        _graph(),
        source_kind=SOURCE_AGENT_SESSION,
        source_span=SPAN_INTERMEDIATE_HYPOTHESIS,
        source_id="session:1",
        anchor_node_ids=("file:repo:test:src/auth.py",),
        source_refs=(SemanticFactSourceRef(ref_id="session:1", ref_kind="session"),),
        payload={"hypothesis": "Maybe this should raise."},
    )

    assert not build.passed
    assert build.packet is None
    assert any(item["reason"] == "agent_session_intermediate_hypothesis_excluded" for item in build.diagnostics)


def test_provider_parser_accepts_strict_json() -> None:
    parsed = parse_semantic_fact_proposals(
        {
            "facts": [
                {
                    "fact_type": "implementation_rationale",
                    "text": "Login returns None because older route handlers treat missing users as anonymous sessions.",
                    "anchor_node_ids": ["file:repo:test:src/auth.py"],
                    "source_refs": [{"ref_id": "commit:abc123", "ref_kind": "commit"}],
                    "derivability": REQUIRES_GIT_HISTORY,
                    "source_kind": SOURCE_HUMAN_COMMIT,
                    "source_span": SPAN_COMMIT_MESSAGE,
                    "fact_scope": ANCHOR_LOCAL_SCOPE,
                    "confidence": 0.81,
                }
            ]
        }
    )

    assert parsed.passed
    assert parsed.proposals[0].fact_type == "implementation_rationale"
    assert parsed.proposals[0].source_refs[0].ref_id == "commit:abc123"


def test_provider_parser_and_review_reject_generic_fact() -> None:
    parsed = parse_semantic_fact_proposals(
        {
            "facts": [
                {
                    "fact_type": "implementation_rationale",
                    "text": "Updated the function.",
                    "anchor_node_ids": ["file:repo:test:src/auth.py"],
                    "source_refs": [{"ref_id": "commit:abc123", "ref_kind": "commit"}],
                    "derivability": REQUIRES_GIT_HISTORY,
                    "source_kind": SOURCE_HUMAN_COMMIT,
                    "source_span": SPAN_COMMIT_MESSAGE,
                }
            ]
        }
    )
    review = review_semantic_fact_proposals(graph=_graph(), proposals=parsed.proposals)

    assert parsed.passed
    assert len(review.rejected_facts) == 1
    assert any(item["reason"] == "generic_fact_text" for item in review.diagnostics)


def test_stale_verified_fact_is_not_authoritative_in_context() -> None:
    proposal = SemanticFactProposal(
        fact_type="risk_or_impact",
        text="Changing login None semantics breaks anonymous-session route handling.",
        anchor_node_ids=("file:repo:test:src/auth.py",),
        source_refs=(SemanticFactSourceRef(ref_id="commit:abc123", ref_kind="commit"),),
        derivability=REQUIRES_GIT_HISTORY,
        source_kind=SOURCE_HUMAN_COMMIT,
        source_span=SPAN_COMMIT_MESSAGE,
        confidence=0.86,
        verified_against_commit="abc123",
        verification_status=VERIFIED_AT_COMMIT,
    )
    review = review_semantic_fact_proposals(graph=_graph(), proposals=(proposal,))

    stale = mark_stale_facts_for_changed_anchors(
        review.accepted_facts,
        changed_anchor_node_ids=("file:repo:test:src/auth.py",),
        changed_after_commit="def456",
    )
    graph = _graph_with_fact(stale.facts[0].as_dict())
    result = answer_context_for_anchor(
        graph,
        files=("src/auth.py",),
        questions=("what will break if I change this?",),
    )

    assert stale.facts[0].review_status == REVIEW_REVIEW_ONLY
    assert stale.facts[0].verification_status == STALE_RISK
    assert result.status == "partial_structural"
    assert result.answers[0].review_status == "missing"


def _graph() -> StructuralHarnessGraph:
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(
                id="file:repo:test:src/auth.py",
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                metadata={"path": "src/auth.py"},
            ),
        ),
        edges=(),
    )


def _graph_with_fact(fact: dict[str, object]) -> StructuralHarnessGraph:
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(
                id="file:repo:test:src/auth.py",
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                metadata={"path": "src/auth.py", "semantic_facts": (fact,)},
            ),
        ),
        edges=(),
    )
