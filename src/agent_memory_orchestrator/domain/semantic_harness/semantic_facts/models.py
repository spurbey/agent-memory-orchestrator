from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any


DERIVABLE_FROM_CURRENT_CODE = "derivable_from_current_code"
DERIVABLE_FROM_DOCS = "derivable_from_docs"
REQUIRES_GIT_HISTORY = "requires_git_history"
REQUIRES_AGENT_SESSION_HISTORY = "requires_agent_session_history"
REQUIRES_HUMAN_INTENT = "requires_human_intent"
REQUIRES_RUNTIME_OBSERVATION = "requires_runtime_observation"
MIXED_DERIVABILITY = "mixed"
UNKNOWN_DERIVABILITY = "unknown"

REVIEW_ACCEPTED = "accepted"
REVIEW_REVIEW_ONLY = "review_only"
REVIEW_REJECTED = "rejected"
REVIEW_QUARANTINED = "quarantined"
REVIEW_PENDING = "semantic_pending"

ANCHOR_LOCAL_SCOPE = "anchor_local"
RELATIONSHIP_SCOPE = "relationship"
SYSTEM_SCOPE = "system"

SOURCE_CURRENT_CODE = "current_code"
SOURCE_DOCS = "docs"
SOURCE_DOCSTRING = "docstring"
SOURCE_HUMAN_COMMIT = "human_commit"
SOURCE_PULL_REQUEST = "pull_request"
SOURCE_AGENT_SESSION = "agent_session"
SOURCE_MANUAL_ANNOTATION = "manual_annotation"
SOURCE_RUNTIME_OBSERVATION = "runtime_observation"
SOURCE_IMPORTED_HISTORY = "imported_history"

SPAN_VALIDATED_COMMITTED = "validated_committed"
SPAN_FINAL_SUMMARY = "final_summary"
SPAN_INTERMEDIATE_HYPOTHESIS = "intermediate_hypothesis"
SPAN_COMMIT_MESSAGE = "commit_message"
SPAN_PR_BODY = "pr_body"
SPAN_DOC_CLAIM = "doc_claim"
SPAN_MANUAL_NOTE = "manual_note"
SPAN_RUNTIME_OBSERVATION = "runtime_observation"

VERIFIED_CURRENT = "verified_current"
VERIFIED_AT_COMMIT = "verified_at_commit"
UNVERIFIED = "unverified"
STALE_RISK = "stale_risk"

CODE_DERIVABLE = frozenset({DERIVABLE_FROM_CURRENT_CODE, DERIVABLE_FROM_DOCS})
NON_DERIVABLE = frozenset(
    {
        REQUIRES_GIT_HISTORY,
        REQUIRES_AGENT_SESSION_HISTORY,
        REQUIRES_HUMAN_INTENT,
        REQUIRES_RUNTIME_OBSERVATION,
        MIXED_DERIVABILITY,
    }
)
TRUSTED_REVIEW_STATUSES = frozenset({REVIEW_ACCEPTED})
FACT_SCOPES = frozenset({ANCHOR_LOCAL_SCOPE, RELATIONSHIP_SCOPE, SYSTEM_SCOPE})
DOC_SOURCE_KINDS = frozenset({SOURCE_DOCS, SOURCE_DOCSTRING})
SESSION_SOURCE_KINDS = frozenset({SOURCE_AGENT_SESSION})


@dataclass(slots=True, frozen=True)
class SemanticFactSourceRef:
    ref_id: str
    ref_kind: str
    path: str = ""
    line: int = 0
    node_id: str = ""
    edge_key: tuple[str, str, str] | None = None
    excerpt: str = ""

    def as_dict(self) -> dict[str, str]:
        payload = {
            "ref_id": self.ref_id,
            "ref_kind": self.ref_kind,
            "path": self.path,
            "line": str(self.line) if self.line else "",
            "node_id": self.node_id,
            "excerpt": self.excerpt,
        }
        if self.edge_key is not None:
            payload["edge_key"] = "|".join(self.edge_key)
        return {key: value for key, value in payload.items() if value}


@dataclass(slots=True, frozen=True)
class SemanticFact:
    fact_id: str
    fact_type: str
    text: str
    anchor_node_ids: tuple[str, ...]
    source_refs: tuple[dict[str, str], ...] = ()
    confidence: float = 0.0
    review_status: str = REVIEW_PENDING
    derivability: str = UNKNOWN_DERIVABILITY
    discovery_cost: str = "unknown"
    source_kind: str = ""
    fact_scope: str = ANCHOR_LOCAL_SCOPE
    source_span: str = ""
    as_of_commit: str = ""
    verified_against_commit: str = ""
    verification_status: str = UNVERIFIED
    trust_tier: int = 99

    @property
    def trusted(self) -> bool:
        return self.review_status in TRUSTED_REVIEW_STATUSES and bool(self.text.strip())

    @property
    def non_derivable(self) -> bool:
        return self.derivability in NON_DERIVABLE

    def as_dict(self) -> dict[str, object]:
        return {
            "fact_id": self.fact_id,
            "fact_type": self.fact_type,
            "text": self.text,
            "anchor_node_ids": list(self.anchor_node_ids),
            "source_refs": list(self.source_refs),
            "confidence": self.confidence,
            "review_status": self.review_status,
            "derivability": self.derivability,
            "discovery_cost": self.discovery_cost,
            "source_kind": self.source_kind,
            "fact_scope": self.fact_scope,
            "source_span": self.source_span,
            "as_of_commit": self.as_of_commit,
            "verified_against_commit": self.verified_against_commit,
            "verification_status": self.verification_status,
            "trust_tier": self.trust_tier,
        }


@dataclass(slots=True, frozen=True)
class SemanticFactProposal:
    fact_type: str
    text: str
    anchor_node_ids: tuple[str, ...]
    source_refs: tuple[SemanticFactSourceRef, ...]
    derivability: str
    source_kind: str
    fact_scope: str = ANCHOR_LOCAL_SCOPE
    source_span: str = ""
    confidence: float = 0.0
    discovery_cost: str = "unknown"
    as_of_commit: str = ""
    verified_against_commit: str = ""
    verification_status: str = UNVERIFIED
    proposal_id: str = ""

    @property
    def resolved_proposal_id(self) -> str:
        return self.proposal_id or semantic_fact_id(
            fact_type=self.fact_type,
            text=self.text,
            anchor_node_ids=self.anchor_node_ids,
            source_refs=tuple(ref.as_dict() for ref in self.source_refs),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.resolved_proposal_id,
            "fact_type": self.fact_type,
            "text": self.text,
            "anchor_node_ids": list(self.anchor_node_ids),
            "source_refs": [ref.as_dict() for ref in self.source_refs],
            "derivability": self.derivability,
            "source_kind": self.source_kind,
            "fact_scope": self.fact_scope,
            "source_span": self.source_span,
            "confidence": self.confidence,
            "discovery_cost": self.discovery_cost,
            "as_of_commit": self.as_of_commit,
            "verified_against_commit": self.verified_against_commit,
            "verification_status": self.verification_status,
        }


def semantic_fact_id(
    *,
    fact_type: str,
    text: str,
    anchor_node_ids: tuple[str, ...],
    source_refs: tuple[dict[str, str], ...],
) -> str:
    stable = "|".join(
        [
            fact_type.strip(),
            text.strip(),
            ",".join(sorted(anchor_node_ids)),
            repr(tuple(sorted(tuple(sorted(ref.items())) for ref in source_refs))),
        ]
    )
    return f"semantic_fact:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"


def reviewed_fact_from_proposal(
    proposal: SemanticFactProposal,
    *,
    review_status: str,
    confidence: float | None = None,
) -> SemanticFact:
    source_refs = tuple(ref.as_dict() for ref in proposal.source_refs)
    trust_tier = semantic_fact_trust_tier(
        source_kind=proposal.source_kind,
        derivability=proposal.derivability,
        verification_status=proposal.verification_status,
    )
    return SemanticFact(
        fact_id=proposal.resolved_proposal_id,
        fact_type=proposal.fact_type,
        text=proposal.text.strip(),
        anchor_node_ids=proposal.anchor_node_ids,
        source_refs=source_refs,
        confidence=_bounded_float(confidence if confidence is not None else proposal.confidence),
        review_status=review_status,
        derivability=proposal.derivability,
        discovery_cost=proposal.discovery_cost,
        source_kind=proposal.source_kind,
        fact_scope=proposal.fact_scope,
        source_span=proposal.source_span,
        as_of_commit=proposal.as_of_commit,
        verified_against_commit=proposal.verified_against_commit,
        verification_status=proposal.verification_status,
        trust_tier=trust_tier,
    )


def semantic_fact_trust_tier(
    *,
    source_kind: str,
    derivability: str,
    verification_status: str = UNVERIFIED,
) -> int:
    if source_kind == SOURCE_MANUAL_ANNOTATION:
        return 1
    if source_kind in {SOURCE_HUMAN_COMMIT, SOURCE_PULL_REQUEST} and derivability in NON_DERIVABLE:
        return 2
    if source_kind == SOURCE_AGENT_SESSION and derivability in NON_DERIVABLE:
        return 3
    if source_kind in DOC_SOURCE_KINDS:
        return 4 if verification_status in {VERIFIED_CURRENT, VERIFIED_AT_COMMIT} else 6
    if derivability == DERIVABLE_FROM_CURRENT_CODE:
        return 5
    return 7


def _bounded_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(min(1.0, max(0.0, number)), 2)


__all__ = [
    "ANCHOR_LOCAL_SCOPE",
    "CODE_DERIVABLE",
    "DERIVABLE_FROM_CURRENT_CODE",
    "DERIVABLE_FROM_DOCS",
    "DOC_SOURCE_KINDS",
    "FACT_SCOPES",
    "MIXED_DERIVABILITY",
    "NON_DERIVABLE",
    "RELATIONSHIP_SCOPE",
    "REQUIRES_AGENT_SESSION_HISTORY",
    "REQUIRES_GIT_HISTORY",
    "REQUIRES_HUMAN_INTENT",
    "REQUIRES_RUNTIME_OBSERVATION",
    "REVIEW_ACCEPTED",
    "REVIEW_PENDING",
    "REVIEW_QUARANTINED",
    "REVIEW_REJECTED",
    "REVIEW_REVIEW_ONLY",
    "SESSION_SOURCE_KINDS",
    "SOURCE_AGENT_SESSION",
    "SOURCE_CURRENT_CODE",
    "SOURCE_DOCS",
    "SOURCE_DOCSTRING",
    "SOURCE_HUMAN_COMMIT",
    "SOURCE_IMPORTED_HISTORY",
    "SOURCE_MANUAL_ANNOTATION",
    "SOURCE_PULL_REQUEST",
    "SOURCE_RUNTIME_OBSERVATION",
    "SPAN_COMMIT_MESSAGE",
    "SPAN_DOC_CLAIM",
    "SPAN_FINAL_SUMMARY",
    "SPAN_INTERMEDIATE_HYPOTHESIS",
    "SPAN_MANUAL_NOTE",
    "SPAN_PR_BODY",
    "SPAN_RUNTIME_OBSERVATION",
    "SPAN_VALIDATED_COMMITTED",
    "STALE_RISK",
    "SYSTEM_SCOPE",
    "TRUSTED_REVIEW_STATUSES",
    "UNKNOWN_DERIVABILITY",
    "UNVERIFIED",
    "VERIFIED_AT_COMMIT",
    "VERIFIED_CURRENT",
    "SemanticFact",
    "SemanticFactProposal",
    "SemanticFactSourceRef",
    "reviewed_fact_from_proposal",
    "semantic_fact_id",
    "semantic_fact_trust_tier",
]
