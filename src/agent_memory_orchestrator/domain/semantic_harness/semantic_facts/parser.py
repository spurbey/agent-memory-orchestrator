from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .models import SemanticFactProposal
from .models import SemanticFactSourceRef


SUPPORTED_SEMANTIC_FACT_TYPES = frozenset(
    {
        "semantic_role",
        "invariant_or_contract",
        "implementation_rationale",
        "risk_or_impact",
        "relationship_reason",
        "validation_expectation",
        "historical_change",
    }
)


@dataclass(slots=True, frozen=True)
class SemanticFactProposalParse:
    proposals: tuple[SemanticFactProposal, ...]
    diagnostics: tuple[dict[str, str], ...]

    @property
    def passed(self) -> bool:
        return bool(self.proposals) and not any(item.get("level") == "error" for item in self.diagnostics)

    def as_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "proposals": [proposal.as_dict() for proposal in self.proposals],
            "diagnostics": list(self.diagnostics),
        }


def parse_semantic_fact_proposals(value: str | dict[str, Any] | list[Any]) -> SemanticFactProposalParse:
    parsed, diagnostics = _parse_payload(value)
    if parsed is None:
        return SemanticFactProposalParse(proposals=(), diagnostics=tuple(diagnostics))
    raw_facts = parsed.get("facts") if isinstance(parsed, dict) else parsed
    if not isinstance(raw_facts, list):
        return SemanticFactProposalParse(
            proposals=(),
            diagnostics=(*diagnostics, _diag("error", "facts_not_list")),
        )
    proposals: list[SemanticFactProposal] = []
    for index, raw in enumerate(raw_facts):
        proposal, item_diagnostics = _parse_fact(raw, index=index)
        diagnostics.extend(item_diagnostics)
        if proposal is not None:
            proposals.append(proposal)
    return SemanticFactProposalParse(proposals=tuple(proposals), diagnostics=tuple(diagnostics))


def _parse_payload(value: str | dict[str, Any] | list[Any]) -> tuple[dict[str, Any] | list[Any] | None, list[dict[str, str]]]:
    if isinstance(value, (dict, list)):
        return value, []
    try:
        parsed = json.loads(str(value or ""))
    except json.JSONDecodeError as exc:
        return None, [_diag("error", "invalid_json", value=str(exc))]
    if not isinstance(parsed, (dict, list)):
        return None, [_diag("error", "payload_not_object_or_list")]
    return parsed, []


def _parse_fact(raw: Any, *, index: int) -> tuple[SemanticFactProposal | None, list[dict[str, str]]]:
    diagnostics: list[dict[str, str]] = []
    if not isinstance(raw, dict):
        return None, [_diag("error", "fact_not_object", index=index)]
    fact_type = str(raw.get("fact_type") or raw.get("type") or "").strip()
    if fact_type not in SUPPORTED_SEMANTIC_FACT_TYPES:
        diagnostics.append(_diag("error", "unsupported_fact_type", index=index, value=fact_type))
    text = str(raw.get("text") or raw.get("summary") or "").strip()
    if not text:
        diagnostics.append(_diag("error", "missing_text", index=index))
    anchors = _tuple_of_strings(raw.get("anchor_node_ids"))
    if not anchors:
        diagnostics.append(_diag("error", "missing_anchor_node_ids", index=index))
    source_refs = _source_refs(raw.get("source_refs"), index=index, diagnostics=diagnostics)
    derivability = str(raw.get("derivability") or "").strip()
    if not derivability:
        diagnostics.append(_diag("error", "missing_derivability", index=index))
    source_kind = str(raw.get("source_kind") or "").strip()
    if not source_kind:
        diagnostics.append(_diag("error", "missing_source_kind", index=index))
    if any(item["level"] == "error" for item in diagnostics):
        return None, diagnostics
    return (
        SemanticFactProposal(
            fact_type=fact_type,
            text=text,
            anchor_node_ids=anchors,
            source_refs=tuple(source_refs),
            derivability=derivability,
            source_kind=source_kind,
            fact_scope=str(raw.get("fact_scope") or "anchor_local"),
            source_span=str(raw.get("source_span") or ""),
            confidence=_bounded_float(raw.get("confidence")),
            discovery_cost=str(raw.get("discovery_cost") or "unknown"),
            as_of_commit=str(raw.get("as_of_commit") or ""),
            verified_against_commit=str(raw.get("verified_against_commit") or ""),
            verification_status=str(raw.get("verification_status") or "unverified"),
            proposal_id=str(raw.get("proposal_id") or ""),
        ),
        diagnostics,
    )


def _source_refs(value: Any, *, index: int, diagnostics: list[dict[str, str]]) -> list[SemanticFactSourceRef]:
    raw_refs = value if isinstance(value, list) else []
    refs: list[SemanticFactSourceRef] = []
    if not raw_refs:
        diagnostics.append(_diag("error", "missing_source_refs", index=index))
        return refs
    for ref_index, raw_ref in enumerate(raw_refs):
        if not isinstance(raw_ref, dict):
            diagnostics.append(_diag("error", "source_ref_not_object", index=index, value=str(ref_index)))
            continue
        ref_id = str(raw_ref.get("ref_id") or "").strip()
        ref_kind = str(raw_ref.get("ref_kind") or raw_ref.get("kind") or "").strip()
        if not ref_id or not ref_kind:
            diagnostics.append(_diag("error", "invalid_source_ref", index=index, value=str(ref_index)))
            continue
        refs.append(
            SemanticFactSourceRef(
                ref_id=ref_id,
                ref_kind=ref_kind,
                path=str(raw_ref.get("path") or ""),
                line=_bounded_int(raw_ref.get("line")),
                node_id=str(raw_ref.get("node_id") or ""),
                excerpt=str(raw_ref.get("excerpt") or ""),
            )
        )
    return refs


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _bounded_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return round(min(1.0, max(0.0, number)), 2)


def _bounded_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _diag(level: str, reason: str, *, index: int | None = None, value: str = "") -> dict[str, str]:
    out = {"level": level, "reason": reason}
    if index is not None:
        out["index"] = str(index)
    if value:
        out["value"] = value
    return out


__all__ = ["SUPPORTED_SEMANTIC_FACT_TYPES", "SemanticFactProposalParse", "parse_semantic_fact_proposals"]
