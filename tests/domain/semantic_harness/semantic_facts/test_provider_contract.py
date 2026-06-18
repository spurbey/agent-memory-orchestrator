from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REQUIRES_GIT_HISTORY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_HUMAN_COMMIT
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SPAN_COMMIT_MESSAGE
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SemanticFactProposal
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SemanticFactSourceRef
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import build_repo_semantic_fact_prompt
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import repo_semantic_fact_output_schema
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import review_semantic_fact_proposals


def test_repo_semantic_prompt_forbids_invented_anchors_and_generic_facts() -> None:
    prompt = build_repo_semantic_fact_prompt(
        {
            "repo_id": "repo:test",
            "allowed_anchor_node_ids": [{"node_id": "file:repo:test:src/auth.py"}],
            "allowed_source_refs": [{"ref_id": "commit:abc123", "ref_kind": "commit"}],
        }
    )

    assert "Use only anchor_node_ids listed in packet.allowed_anchor_node_ids" in prompt
    assert "Do not invent node ids" in prompt
    assert "generic facts" in prompt
    assert "SemanticFactProposal" in prompt


def test_repo_semantic_output_schema_requires_anchor_and_source_refs() -> None:
    fact_schema = repo_semantic_fact_output_schema()["properties"]["facts"]["items"]

    assert "anchor_node_ids" in fact_schema["required"]
    assert "source_refs" in fact_schema["required"]
    assert "derivability" in fact_schema["required"]


def test_review_rejects_provider_invented_node_ids() -> None:
    proposal = SemanticFactProposal(
        fact_type="implementation_rationale",
        text="The connector contract moved into the domain package to stop domain imports from depending on integrations.",
        anchor_node_ids=("invented:node",),
        source_refs=(SemanticFactSourceRef(ref_id="commit:abc123", ref_kind="commit"),),
        derivability=REQUIRES_GIT_HISTORY,
        source_kind=SOURCE_HUMAN_COMMIT,
        source_span=SPAN_COMMIT_MESSAGE,
        confidence=0.84,
    )

    review = review_semantic_fact_proposals(graph=_graph(), proposals=(proposal,))

    assert len(review.rejected_facts) == 1
    assert any(item["reason"].startswith("missing_anchor_nodes:") for item in review.diagnostics)


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
