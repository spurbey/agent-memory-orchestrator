from __future__ import annotations

import logging
import re
from typing import Any
from typing import Iterable

from ..models import HarnessNode
from ..semantic_facts import CODE_DERIVABLE
from ..semantic_facts import DERIVABLE_FROM_CURRENT_CODE
from ..semantic_facts import DERIVABLE_FROM_DOCS
from ..semantic_facts import MIXED_DERIVABILITY
from ..semantic_facts import NON_DERIVABLE
from ..semantic_facts import REQUIRES_AGENT_SESSION_HISTORY
from ..semantic_facts import REQUIRES_GIT_HISTORY
from ..semantic_facts import REQUIRES_HUMAN_INTENT
from ..semantic_facts import REQUIRES_RUNTIME_OBSERVATION
from ..semantic_facts import REVIEW_ACCEPTED
from ..semantic_facts import REVIEW_PENDING
from ..semantic_facts import REVIEW_QUARANTINED
from ..semantic_facts import REVIEW_REJECTED
from ..semantic_facts import REVIEW_REVIEW_ONLY
from ..semantic_facts import UNKNOWN_DERIVABILITY
from ..semantic_facts import SemanticFact

_LOG = logging.getLogger(__name__)
LOW_RELEVANCE_THRESHOLD = 0.18
TIGHT_RELEVANCE_RATIO = 0.15
_STOPWORDS = frozenset(
    {
        "a",
        "about",
        "an",
        "and",
        "are",
        "as",
        "before",
        "by",
        "change",
        "changed",
        "changes",
        "changing",
        "does",
        "edit",
        "editing",
        "edits",
        "for",
        "from",
        "here",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "me",
        "of",
        "or",
        "should",
        "the",
        "this",
        "to",
        "what",
        "when",
        "where",
        "which",
        "why",
        "will",
        "with",
    }
)


def semantic_facts_for_node(node: HarnessNode, *, fact_types: Iterable[str] = ()) -> tuple[SemanticFact, ...]:
    requested = {fact_type for fact_type in fact_types if fact_type}
    facts = [fact for fact in _metadata_facts(node) if not requested or fact.fact_type in requested]
    if "invariant_or_contract" in requested or not requested:
        facts.extend(_legacy_invariant_facts(node))
    if "semantic_role" in requested or not requested:
        facts.extend(_legacy_summary_facts(node))
    return tuple(_dedupe_facts(facts))


def best_fact_for_node(
    node: HarnessNode,
    *,
    fact_types: Iterable[str],
    prefer_non_derivable: bool,
    question: str = "",
    goal: str = "",
) -> SemanticFact | None:
    ranked = ranked_facts_for_node(
        node,
        fact_types=fact_types,
        prefer_non_derivable=prefer_non_derivable,
        question=question,
        goal=goal,
        limit=1,
    )
    return ranked[0] if ranked else None


def ranked_facts_for_node(
    node: HarnessNode,
    *,
    fact_types: Iterable[str],
    prefer_non_derivable: bool,
    question: str = "",
    goal: str = "",
    limit: int = 1,
    diagnostics: list[str] | None = None,
) -> tuple[SemanticFact, ...]:
    ordered_fact_types = tuple(fact_type for fact_type in fact_types if fact_type)
    type_priority = {fact_type: index for index, fact_type in enumerate(ordered_fact_types)}
    candidates = tuple(fact for fact in semantic_facts_for_node(node, fact_types=fact_types) if fact.trusted)
    if not candidates:
        return ()
    ranked = sorted(
        candidates,
        key=lambda fact: _fact_sort_key(
            fact,
            prefer_non_derivable=prefer_non_derivable,
            question=question,
            goal=goal,
            type_priority=type_priority,
        ),
        reverse=True,
    )
    _append_fact_relevance_diagnostics(ranked, question=question, goal=goal, diagnostics=diagnostics)
    return tuple(ranked[: max(1, limit)])


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
                fact_scope=str(item.get("fact_scope") or "anchor_local"),
                source_span=str(item.get("source_span") or ""),
                as_of_commit=str(item.get("as_of_commit") or ""),
                verified_against_commit=str(item.get("verified_against_commit") or ""),
                verification_status=str(item.get("verification_status") or "unverified"),
                trust_tier=_bounded_int(item.get("trust_tier"), default=99),
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
            fact_scope="anchor_local",
            verification_status=str(node.metadata.get("invariant_verification_status") or "unverified"),
            trust_tier=_bounded_int(node.metadata.get("invariant_trust_tier"), default=5),
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
            fact_scope="anchor_local",
            verification_status=str(node.metadata.get("summary_verification_status") or "unverified"),
            trust_tier=_bounded_int(node.metadata.get("summary_trust_tier"), default=5),
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


def score_fact_relevance(fact: SemanticFact, *, question: str = "", goal: str = "") -> float:
    query_tokens = _tokens(f"{question} {goal}")
    if not query_tokens:
        return 0.0
    fact_tokens = _tokens(f"{fact.fact_type} {fact.text}")
    if not fact_tokens:
        return 0.0
    overlap = len(query_tokens & fact_tokens)
    if not overlap:
        return 0.0
    recall = overlap / len(query_tokens)
    precision = overlap / len(fact_tokens)
    return round((recall * 0.75) + (precision * 0.25), 4)


def _fact_sort_key(
    fact: SemanticFact,
    *,
    prefer_non_derivable: bool,
    question: str = "",
    goal: str = "",
    type_priority: dict[str, int] | None = None,
) -> tuple[float, float, float, float, float, float]:
    type_priority = type_priority or {}
    type_bonus = 1.0 - (type_priority.get(fact.fact_type, 99) * 0.01)
    relevance = score_fact_relevance(fact, question=question, goal=goal)
    non_derivable_bonus = 1.0 if prefer_non_derivable and fact.non_derivable else 0.0
    review_bonus = 1.0 if fact.trusted else 0.0
    trust_bonus = -float(fact.trust_tier)
    return (relevance, type_bonus, non_derivable_bonus, review_bonus, trust_bonus, fact.confidence)


def _tokens(text: str) -> set[str]:
    raw = re.findall(r"[a-zA-Z0-9]+", str(text or "").replace("_", " ").lower())
    return {token for token in raw if len(token) > 2 and token not in _STOPWORDS}


def _append_fact_relevance_diagnostics(
    ranked: list[SemanticFact],
    *,
    question: str,
    goal: str,
    diagnostics: list[str] | None,
) -> None:
    if not ranked:
        return
    top = score_fact_relevance(ranked[0], question=question, goal=goal)
    if top < LOW_RELEVANCE_THRESHOLD:
        _record_diagnostic(
            diagnostics,
            f"low_relevance_fact_choice:top_fact_id={ranked[0].fact_id}:top_score={top:.4f}",
        )
    if len(ranked) < 2 or top <= 0:
        return
    second = score_fact_relevance(ranked[1], question=question, goal=goal)
    if (top - second) <= max(0.01, top * TIGHT_RELEVANCE_RATIO):
        _record_diagnostic(
            diagnostics,
            (
                "tight_fact_relevance_scores:"
                f"top_fact_id={ranked[0].fact_id}:second_fact_id={ranked[1].fact_id}:"
                f"top_score={top:.4f}:second_score={second:.4f}"
            ),
        )
        _LOG.debug(
            "tight_fact_relevance_scores",
            extra={
                "candidate_count": len(ranked),
                "top_score": top,
                "second_score": second,
                "top_fact_id": ranked[0].fact_id,
                "second_fact_id": ranked[1].fact_id,
            },
        )


def _record_diagnostic(diagnostics: list[str] | None, message: str) -> None:
    if diagnostics is not None:
        diagnostics.append(message)


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


def _bounded_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(1, min(99, number))


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
    "ranked_facts_for_node",
    "semantic_facts_for_node",
]
