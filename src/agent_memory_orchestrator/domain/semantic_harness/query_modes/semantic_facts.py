from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from typing import Iterable

from ..models import HarnessNode


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
        }


def semantic_facts_for_node(node: HarnessNode, *, fact_types: Iterable[str] = ()) -> tuple[SemanticFact, ...]:
    requested = {fact_type for fact_type in fact_types if fact_type}
    facts = [fact for fact in _metadata_facts(node) if not requested or fact.fact_type in requested]
    if "invariant_or_contract" in requested or not requested:
        facts.extend(_legacy_invariant_facts(node))
    if "semantic_role" in requested or not requested:
        facts.extend(_legacy_summary_facts(node))
    return tuple(_dedupe_facts(facts))


def best_fact_for_node(node: HarnessNode, *, fact_types: Iterable[str], prefer_non_derivable: bool) -> SemanticFact | None:
    candidates = tuple(fact for fact in semantic_facts_for_node(node, fact_types=fact_types) if fact.trusted)
    if not candidates:
        return None
    return sorted(candidates, key=lambda fact: _fact_sort_key(fact, prefer_non_derivable=prefer_non_derivable), reverse=True)[0]


def facts_need_non_derivable(question_type: str) -> bool:
    return question_type in {"history", "risk"}


def _metadata_facts(node: HarnessNode) -> tuple[SemanticFact, ...]:
    raw = node.metadata.get("semantic_facts") or ()
    if isinstance(raw, dict):
        raw = (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()

    facts: list[SemanticFact] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("summary") or "").strip()
        fact_type = str(item.get("fact_type") or item.get("type") or "").strip()
        if not text or not fact_type:
            continue
        facts.append(
            SemanticFact(
                fact_id=str(item.get("fact_id") or f"{node.id}:semantic_fact:{index}"),
                fact_type=fact_type,
                text=text,
                anchor_node_ids=_tuple_of_strings(item.get("anchor_node_ids") or (node.id,)),
                source_refs=_source_refs(item.get("source_refs") or ()),
                confidence=_bounded_float(item.get("confidence"), default=0.0),
                review_status=str(item.get("review_status") or REVIEW_PENDING),
                derivability=str(item.get("derivability") or UNKNOWN_DERIVABILITY),
                discovery_cost=str(item.get("discovery_cost") or "unknown"),
                source_kind=str(item.get("source_kind") or item.get("source") or ""),
            )
        )
    return tuple(facts)


def _legacy_invariant_facts(node: HarnessNode) -> tuple[SemanticFact, ...]:
    text = str(node.metadata.get("invariant") or node.metadata.get("constraint") or "").strip()
    if not text:
        return ()
    return (
        SemanticFact(
            fact_id=f"{node.id}:legacy_invariant",
            fact_type="invariant_or_contract",
            text=text,
            anchor_node_ids=(node.id,),
            source_refs=(_node_source_ref(node),),
            confidence=_bounded_float(node.metadata.get("invariant_confidence"), default=0.76),
            review_status=str(node.metadata.get("invariant_review_status") or REVIEW_ACCEPTED),
            derivability=str(node.metadata.get("invariant_derivability") or DERIVABLE_FROM_CURRENT_CODE),
            discovery_cost=str(node.metadata.get("invariant_discovery_cost") or "medium"),
            source_kind=str(node.metadata.get("invariant_source_kind") or "legacy_metadata"),
        ),
    )


def _legacy_summary_facts(node: HarnessNode) -> tuple[SemanticFact, ...]:
    text = str(node.summary or "").strip()
    if not text or _looks_like_boilerplate_summary(text):
        return ()
    return (
        SemanticFact(
            fact_id=f"{node.id}:legacy_summary",
            fact_type="semantic_role",
            text=text,
            anchor_node_ids=(node.id,),
            source_refs=(_node_source_ref(node),),
            confidence=_bounded_float(node.metadata.get("summary_confidence"), default=0.72),
            review_status=str(node.metadata.get("summary_review_status") or REVIEW_ACCEPTED),
            derivability=str(node.metadata.get("summary_derivability") or DERIVABLE_FROM_CURRENT_CODE),
            discovery_cost=str(node.metadata.get("summary_discovery_cost") or "low"),
            source_kind=str(node.metadata.get("summary_source_kind") or "structural_summary"),
        ),
    )


def _looks_like_boilerplate_summary(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized.startswith(("from __future__ import ", "import ")) or normalized in {"pass", "..."}


def _dedupe_facts(facts: list[SemanticFact]) -> list[SemanticFact]:
    out: list[SemanticFact] = []
    seen: set[tuple[str, str, str]] = set()
    for fact in facts:
        key = (fact.fact_type, fact.text, "|".join(fact.anchor_node_ids))
        if key in seen:
            continue
        seen.add(key)
        out.append(fact)
    return out


def _fact_sort_key(fact: SemanticFact, *, prefer_non_derivable: bool) -> tuple[float, float, float]:
    non_derivable_bonus = 1.0 if prefer_non_derivable and fact.non_derivable else 0.0
    review_bonus = 1.0 if fact.trusted else 0.0
    return (non_derivable_bonus, review_bonus, fact.confidence)


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item) for item in value if str(item))
    return ()


def _source_refs(value: Any) -> tuple[dict[str, str], ...]:
    if isinstance(value, dict):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    refs: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            refs.append({str(key): str(val) for key, val in item.items() if val is not None})
    return tuple(refs)


def _node_source_ref(node: HarnessNode) -> dict[str, str]:
    return {
        "node_id": node.id,
        "kind": node.kind,
        "label": node.label,
        "path": str(node.metadata.get("path") or ""),
    }


def _bounded_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return round(min(1.0, max(0.0, number)), 2)


__all__ = [
    "CODE_DERIVABLE",
    "DERIVABLE_FROM_CURRENT_CODE",
    "DERIVABLE_FROM_DOCS",
    "MIXED_DERIVABILITY",
    "NON_DERIVABLE",
    "REQUIRES_AGENT_SESSION_HISTORY",
    "REQUIRES_GIT_HISTORY",
    "REQUIRES_HUMAN_INTENT",
    "REQUIRES_RUNTIME_OBSERVATION",
    "REVIEW_ACCEPTED",
    "REVIEW_PENDING",
    "REVIEW_QUARANTINED",
    "REVIEW_REJECTED",
    "REVIEW_REVIEW_ONLY",
    "SemanticFact",
    "UNKNOWN_DERIVABILITY",
    "best_fact_for_node",
    "facts_need_non_derivable",
    "semantic_facts_for_node",
]
