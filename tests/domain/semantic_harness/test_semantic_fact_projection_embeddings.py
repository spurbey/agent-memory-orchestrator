from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.projection import build_projection_documents
from agent_memory_orchestrator.domain.semantic_harness.projection import build_projection_set
from agent_memory_orchestrator.domain.semantic_harness.projection import build_semantic_fact_projection_documents
from agent_memory_orchestrator.domain.semantic_harness.retrieval import build_hash_embedding_manifest
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REQUIRES_GIT_HISTORY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REVIEW_ACCEPTED
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import REVIEW_REVIEW_ONLY
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import SOURCE_HUMAN_COMMIT


def test_projection_includes_accepted_semantic_fact_chunks() -> None:
    graph = _graph_with_facts(
        (
            {
                "fact_id": "fact:accepted",
                "fact_type": "implementation_rationale",
                "text": "Login returns None because route handlers treat missing users as anonymous sessions.",
                "anchor_node_ids": ["file:repo:test:src/auth.py"],
                "source_refs": [{"ref_id": "commit:abc123", "ref_kind": "commit"}],
                "confidence": 0.83,
                "review_status": REVIEW_ACCEPTED,
                "derivability": REQUIRES_GIT_HISTORY,
                "source_kind": SOURCE_HUMAN_COMMIT,
                "fact_scope": "anchor_local",
                "verification_status": "verified_at_commit",
                "trust_tier": 2,
            },
            {
                "fact_id": "fact:review-only",
                "fact_type": "implementation_rationale",
                "text": "This unverified doc claim should not project in normal mode.",
                "anchor_node_ids": ["file:repo:test:src/auth.py"],
                "source_refs": [{"ref_id": "doc:auth", "ref_kind": "doc"}],
                "review_status": REVIEW_REVIEW_ONLY,
                "derivability": "derivable_from_docs",
                "source_kind": "docs",
                "fact_scope": "anchor_local",
                "trust_tier": 6,
            },
        )
    )

    docs = build_projection_documents(graph)
    semantic_docs = tuple(doc for doc in docs if doc.metadata.get("projection_source") == "semantic_harness_semantic_fact")

    assert len(semantic_docs) == 1
    assert semantic_docs[0].doc_type == "semantic_fact_summary"
    assert semantic_docs[0].metadata["fact_id"] == "fact:accepted"
    assert semantic_docs[0].metadata["trust_tier"] == 2
    assert "Login returns None" in semantic_docs[0].text


def test_review_only_projection_is_audit_opt_in() -> None:
    graph = _graph_with_facts(
        (
            {
                "fact_id": "fact:review-only",
                "fact_type": "implementation_rationale",
                "text": "This unverified doc claim is audit-only.",
                "anchor_node_ids": ["file:repo:test:src/auth.py"],
                "source_refs": [{"ref_id": "doc:auth", "ref_kind": "doc"}],
                "review_status": REVIEW_REVIEW_ONLY,
                "derivability": "derivable_from_docs",
                "source_kind": "docs",
                "fact_scope": "anchor_local",
                "trust_tier": 6,
            },
        )
    )

    normal = build_semantic_fact_projection_documents(graph)
    audit = build_semantic_fact_projection_documents(graph, include_review_only=True)

    assert normal == ()
    assert len(audit) == 1
    assert audit[0].metadata["review_status"] == REVIEW_REVIEW_ONLY


def test_embedding_manifest_reuses_rebuilds_and_tombstones_projection_docs() -> None:
    graph = _graph_with_facts(
        (
            {
                "fact_id": "fact:accepted",
                "fact_type": "implementation_rationale",
                "text": "Login returns None because route handlers treat missing users as anonymous sessions.",
                "anchor_node_ids": ["file:repo:test:src/auth.py"],
                "source_refs": [{"ref_id": "commit:abc123", "ref_kind": "commit"}],
                "review_status": REVIEW_ACCEPTED,
                "derivability": REQUIRES_GIT_HISTORY,
                "source_kind": SOURCE_HUMAN_COMMIT,
                "fact_scope": "anchor_local",
                "trust_tier": 2,
            },
        )
    )
    projection = build_projection_set(graph)
    first = build_hash_embedding_manifest(projection_id=projection.projection_id, documents=projection.documents)
    second = build_hash_embedding_manifest(projection_id=projection.projection_id, documents=projection.documents, previous=first)
    changed_projection = build_projection_set(
        _graph_with_facts(
            (
                {
                    "fact_id": "fact:accepted",
                    "fact_type": "implementation_rationale",
                    "text": "Login returns None because legacy route handlers swallow missing-user sessions.",
                    "anchor_node_ids": ["file:repo:test:src/auth.py"],
                    "source_refs": [{"ref_id": "commit:abc123", "ref_kind": "commit"}],
                    "review_status": REVIEW_ACCEPTED,
                    "derivability": REQUIRES_GIT_HISTORY,
                    "source_kind": SOURCE_HUMAN_COMMIT,
                    "fact_scope": "anchor_local",
                    "trust_tier": 2,
                },
            )
        )
    )
    changed = build_hash_embedding_manifest(projection_id=changed_projection.projection_id, documents=changed_projection.documents, previous=second)
    empty = build_hash_embedding_manifest(projection_id="hproj:empty", documents=(), previous=changed)

    assert first.embedded_count == projection.document_count
    assert second.reused_count == projection.document_count
    assert changed.embedded_count == 1
    assert empty.tombstoned_count == changed.doc_count


def _graph_with_facts(facts: tuple[dict[str, object], ...]) -> StructuralHarnessGraph:
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(
                id="file:repo:test:src/auth.py",
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                summary="Auth file.",
                metadata={"path": "src/auth.py", "semantic_facts": facts},
            ),
        ),
        edges=(),
    )
