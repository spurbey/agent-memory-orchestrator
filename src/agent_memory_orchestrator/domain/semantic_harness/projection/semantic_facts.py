from __future__ import annotations

from ..identity import projection_doc_id
from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from ..query_modes.semantic_facts import semantic_facts_for_node
from ..semantic_facts import REVIEW_ACCEPTED
from ..semantic_facts import REVIEW_REVIEW_ONLY
from ..semantic_facts import SemanticFact
from .models import HarnessProjectionDocument


def build_semantic_fact_projection_documents(
    graph: StructuralHarnessGraph,
    *,
    include_review_only: bool = False,
) -> tuple[HarnessProjectionDocument, ...]:
    docs: list[HarnessProjectionDocument] = []
    for node in sorted(graph.nodes, key=lambda item: item.id):
        for fact in semantic_facts_for_node(node):
            if fact.fact_id.startswith(f"{node.id}:legacy_"):
                continue
            if not _projectable(fact, include_review_only=include_review_only):
                continue
            docs.append(_semantic_fact_doc(graph.repo_id, node, fact))
    return tuple(docs)


def _projectable(fact: SemanticFact, *, include_review_only: bool) -> bool:
    if fact.review_status == REVIEW_ACCEPTED:
        return True
    return include_review_only and fact.review_status == REVIEW_REVIEW_ONLY


def _semantic_fact_doc(repo_id: str, node: HarnessNode, fact: SemanticFact) -> HarnessProjectionDocument:
    doc_type = _doc_type_for_fact(fact)
    title = f"{_title_for_fact(fact)}: {node.label}"
    text = _compact_lines(
        (
            title,
            f"fact_type: {fact.fact_type}",
            f"scope: {fact.fact_scope}",
            f"derivability: {fact.derivability}",
            f"source_kind: {fact.source_kind}",
            f"verification_status: {fact.verification_status}",
            f"trust_tier: {fact.trust_tier}",
            f"text: {fact.text}",
        )
    )
    return HarnessProjectionDocument(
        doc_id=projection_doc_id(repo_id, f"{fact.fact_id}:{node.id}", doc_type),
        repo_id=repo_id,
        source_node_id=node.id,
        source_kind=node.kind,
        doc_type=doc_type,
        title=title,
        text=text,
        metadata={
            "path": node.metadata.get("path", ""),
            "status": node.status,
            "projection_source": "semantic_harness_semantic_fact",
            "anchor_node_id": node.id,
            "fact_id": fact.fact_id,
            "fact_type": fact.fact_type,
            "fact_scope": fact.fact_scope,
            "anchor_node_ids": list(fact.anchor_node_ids),
            "review_status": fact.review_status,
            "derivability": fact.derivability,
            "source_kind": fact.source_kind,
            "source_span": fact.source_span,
            "verification_status": fact.verification_status,
            "trust_tier": fact.trust_tier,
            "source_refs": list(fact.source_refs),
        },
    )


def _doc_type_for_fact(fact: SemanticFact) -> str:
    if fact.fact_scope == "relationship":
        return "relationship_fact_summary"
    if fact.source_kind in {"docs", "docstring"}:
        return "doc_claim_summary"
    if fact.source_kind == "agent_session":
        return "work_window_fact_summary"
    return "semantic_fact_summary"


def _title_for_fact(fact: SemanticFact) -> str:
    return fact.fact_type.replace("_", " ").title()


def _compact_lines(lines) -> str:
    return "\n".join(str(line).strip() for line in lines if str(line or "").strip())


__all__ = ["build_semantic_fact_projection_documents"]
