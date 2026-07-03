from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import StructuralHarnessGraph
from .models import ANCHOR_LOCAL_SCOPE
from .models import DERIVABLE_FROM_DOCS
from .models import DOC_SOURCE_KINDS
from .models import FACT_SCOPES
from .models import RELATIONSHIP_SCOPE
from .models import REVIEW_ACCEPTED
from .models import REVIEW_REJECTED
from .models import REVIEW_REVIEW_ONLY
from .models import SOURCE_AGENT_SESSION
from .models import SPAN_FINAL_SUMMARY
from .models import SPAN_INTERMEDIATE_HYPOTHESIS
from .models import SPAN_VALIDATED_COMMITTED
from .models import SYSTEM_SCOPE
from .models import UNVERIFIED
from .models import SemanticFact
from .models import SemanticFactProposal
from .models import reviewed_fact_from_proposal


GENERIC_FACT_PATTERNS = (
    r"\b(modified|updated|changed|edited|touched)\s+(the\s+)?(file|function|code|logic)\b",
    r"\b(fixed|improved)\s+(the\s+)?(bug|issue|code)\b",
    r"\b(adds?|added)\s+(tests?|validation)\b",
)
VALID_AGENT_SESSION_SPANS = frozenset({SPAN_VALIDATED_COMMITTED, SPAN_FINAL_SUMMARY})


@dataclass(slots=True, frozen=True)
class SemanticFactReview:
    accepted_facts: tuple[SemanticFact, ...]
    review_only_facts: tuple[SemanticFact, ...]
    rejected_facts: tuple[SemanticFact, ...]
    diagnostics: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted_facts": [fact.as_dict() for fact in self.accepted_facts],
            "review_only_facts": [fact.as_dict() for fact in self.review_only_facts],
            "rejected_facts": [fact.as_dict() for fact in self.rejected_facts],
            "diagnostics": list(self.diagnostics),
            "summary": {
                "accepted": len(self.accepted_facts),
                "review_only": len(self.review_only_facts),
                "rejected": len(self.rejected_facts),
            },
        }


def review_semantic_fact_proposals(
    *,
    graph: StructuralHarnessGraph,
    proposals: tuple[SemanticFactProposal, ...],
) -> SemanticFactReview:
    accepted: list[SemanticFact] = []
    review_only: list[SemanticFact] = []
    rejected: list[SemanticFact] = []
    diagnostics: list[dict[str, str]] = []

    node_ids = set(graph.node_by_id())
    for proposal in proposals:
        status, reasons = _review_proposal(proposal, node_ids=node_ids)
        diagnostics.extend(_diagnostic(proposal, reason, status=status) for reason in reasons)
        if status == REVIEW_ACCEPTED:
            accepted.append(reviewed_fact_from_proposal(proposal, review_status=REVIEW_ACCEPTED))
        elif status == REVIEW_REVIEW_ONLY:
            review_only.append(reviewed_fact_from_proposal(proposal, review_status=REVIEW_REVIEW_ONLY))
        else:
            rejected.append(reviewed_fact_from_proposal(proposal, review_status=REVIEW_REJECTED, confidence=0.0))

    return SemanticFactReview(
        accepted_facts=tuple(accepted),
        review_only_facts=tuple(review_only),
        rejected_facts=tuple(rejected),
        diagnostics=tuple(diagnostics),
    )


def _review_proposal(proposal: SemanticFactProposal, *, node_ids: set[str]) -> tuple[str, tuple[str, ...]]:
    hard_errors: list[str] = []
    review_warnings: list[str] = []

    if not proposal.fact_type.strip():
        hard_errors.append("missing_fact_type")
    if not proposal.text.strip():
        hard_errors.append("missing_text")
    if proposal.fact_scope not in FACT_SCOPES:
        hard_errors.append("invalid_fact_scope")
    if not proposal.source_refs:
        hard_errors.append("missing_source_refs")
    if _is_generic_fact_text(proposal.text):
        hard_errors.append("generic_fact_text")
    hard_errors.extend(_anchor_errors(proposal, node_ids=node_ids))
    if proposal.source_kind == SOURCE_AGENT_SESSION and proposal.source_span not in VALID_AGENT_SESSION_SPANS:
        hard_errors.append("agent_session_unvalidated_span")
    if proposal.source_span == SPAN_INTERMEDIATE_HYPOTHESIS:
        hard_errors.append("intermediate_hypothesis_not_graph_truth")

    if proposal.source_kind in DOC_SOURCE_KINDS:
        if proposal.verification_status == UNVERIFIED or not proposal.verified_against_commit:
            review_warnings.append("doc_fact_unverified_current")
        if proposal.derivability != DERIVABLE_FROM_DOCS:
            review_warnings.append("doc_fact_derivability_mismatch")

    if hard_errors:
        return REVIEW_REJECTED, tuple(hard_errors)
    if review_warnings:
        return REVIEW_REVIEW_ONLY, tuple(review_warnings)
    return REVIEW_ACCEPTED, ("accepted",)


def _anchor_errors(proposal: SemanticFactProposal, *, node_ids: set[str]) -> list[str]:
    anchor_ids = tuple(proposal.anchor_node_ids)
    missing = tuple(anchor_id for anchor_id in anchor_ids if anchor_id not in node_ids)
    errors: list[str] = []
    if proposal.fact_scope == ANCHOR_LOCAL_SCOPE and len(anchor_ids) < 1:
        errors.append("anchor_local_fact_missing_anchor")
    if proposal.fact_scope == RELATIONSHIP_SCOPE and len(anchor_ids) < 2:
        errors.append("relationship_fact_needs_two_anchors")
    if proposal.fact_scope == SYSTEM_SCOPE and len(anchor_ids) > 8:
        errors.append("system_fact_has_too_many_anchors")
    if missing:
        errors.append("missing_anchor_nodes:" + ",".join(sorted(missing)[:6]))
    return errors


def _is_generic_fact_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return any(re.search(pattern, normalized) for pattern in GENERIC_FACT_PATTERNS)


def _diagnostic(proposal: SemanticFactProposal, reason: str, *, status: str) -> dict[str, str]:
    return {
        "proposal_id": proposal.resolved_proposal_id,
        "status": status,
        "reason": reason,
        "fact_type": proposal.fact_type,
        "source_kind": proposal.source_kind,
    }


__all__ = ["SemanticFactReview", "review_semantic_fact_proposals"]
