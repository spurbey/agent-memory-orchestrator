from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import StructuralHarnessGraph


@dataclass(slots=True, frozen=True)
class HistoricalRelationPolicy:
    """Conservative gate for agent-facing historical relation cards."""

    min_stored_strength: float = 0.40
    min_cochange_count: int = 3
    max_cards_per_anchor: int = 1
    max_occurrences_per_card: int = 3
    require_task_relevant_occurrence: bool = False


@dataclass(slots=True, frozen=True)
class HistoricalOccurrenceMatch:
    node: HarnessNode
    relevance_score: int
    matched_terms: tuple[str, ...]
    relevance_status: str


@dataclass(slots=True, frozen=True)
class HistoricalRelationCandidate:
    edge: HarnessEdge
    anchor_id: str
    related_id: str
    related_node: HarnessNode
    occurrence_matches: tuple[HistoricalOccurrenceMatch, ...]
    score: float

    @property
    def occurrence_nodes(self) -> tuple[HarnessNode, ...]:
        return tuple(match.node for match in self.occurrence_matches)


def historical_relation_candidates(
    graph: StructuralHarnessGraph,
    *,
    anchor_node_id: str,
    task_text: str = "",
    policy: HistoricalRelationPolicy = HistoricalRelationPolicy(),
) -> tuple[HistoricalRelationCandidate, ...]:
    """Return conservative historical relation candidates for one anchor.

    A high Jaccard score is not enough: tiny history can produce Jaccard 1.0.
    The minimum occurrence gate keeps early/noisy co-changes out of patch cards.
    """

    node_by_id = graph.node_by_id()
    task_terms = _task_terms(task_text)
    out: list[HistoricalRelationCandidate] = []
    for edge in graph.edges:
        if edge.kind != "CO_CHANGED_WITH":
            continue
        if edge.source_id == anchor_node_id:
            related_id = edge.target_id
        elif edge.target_id == anchor_node_id:
            related_id = edge.source_id
        else:
            continue
        if not should_show_historical_relation(edge, policy=policy):
            continue
        related = node_by_id.get(related_id)
        if related is None:
            continue
        occurrence_matches = _occurrence_matches_for_edge(graph, edge, policy=policy, task_terms=task_terms)
        if policy.require_task_relevant_occurrence and not any(match.relevance_score > 0 for match in occurrence_matches):
            continue
        out.append(
            HistoricalRelationCandidate(
                edge=edge,
                anchor_id=anchor_node_id,
                related_id=related_id,
                related_node=related,
                occurrence_matches=occurrence_matches,
                score=_candidate_score(edge),
            )
        )
    out.sort(key=lambda candidate: (-candidate.score, candidate.related_node.label, candidate.related_id))
    return tuple(out[: max(0, policy.max_cards_per_anchor)])


def should_show_historical_relation(
    edge: HarnessEdge,
    *,
    policy: HistoricalRelationPolicy = HistoricalRelationPolicy(),
) -> bool:
    metadata = edge.metadata
    stored_strength = float(metadata.get("stored_strength") or edge.weight or 0.0)
    cochange_count = int(metadata.get("cochange_count") or 0)
    return stored_strength >= policy.min_stored_strength and cochange_count >= policy.min_cochange_count


def _occurrence_matches_for_edge(
    graph: StructuralHarnessGraph,
    edge: HarnessEdge,
    *,
    policy: HistoricalRelationPolicy,
    task_terms: tuple[str, ...],
) -> tuple[HistoricalOccurrenceMatch, ...]:
    node_by_id = graph.node_by_id()
    occurrence_ids = tuple(str(item) for item in edge.metadata.get("occurrence_ids", ()))
    matches = [
        _occurrence_match(node, task_terms)
        for node_id in occurrence_ids
        if (node := node_by_id.get(node_id)) is not None and node.kind == "RelationOccurrence"
    ]
    matches.sort(
        key=lambda match: (
            -match.relevance_score,
            str(match.node.metadata.get("commit_id") or match.node.id),
        ),
        reverse=False,
    )
    return tuple(matches[: max(0, policy.max_occurrences_per_card)])


def _occurrence_match(node: HarnessNode, task_terms: tuple[str, ...]) -> HistoricalOccurrenceMatch:
    metadata = node.metadata
    occurrence_terms = set(
        _terms_from_text(
            " ".join(
                str(value)
                for value in (
                    node.label,
                    node.summary,
                    metadata.get("reason", ""),
                    metadata.get("commit_message", ""),
                    metadata.get("commit_id", ""),
                )
                if value
            )
        )
    )
    matched_terms = tuple(term for term in task_terms if term in occurrence_terms)
    return HistoricalOccurrenceMatch(
        node=node,
        relevance_score=len(matched_terms),
        matched_terms=matched_terms,
        relevance_status="task_match" if matched_terms else "structural_fallback",
    )


def _task_terms(task_text: str) -> tuple[str, ...]:
    return _terms_from_text(task_text)


def _terms_from_text(text: str) -> tuple[str, ...]:
    terms = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_]+", text.lower()):
        if len(token) < 3 or token in _STOP_TERMS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return tuple(terms)


def _candidate_score(edge: HarnessEdge) -> float:
    metadata = edge.metadata
    stored_strength = float(metadata.get("stored_strength") or edge.weight or 0.0)
    cochange_count = int(metadata.get("cochange_count") or 0)
    # Strength is primary; count only breaks ties among equally strong edges.
    return round(stored_strength + min(0.05, cochange_count * 0.005), 4)


__all__ = [
    "HistoricalRelationCandidate",
    "HistoricalOccurrenceMatch",
    "HistoricalRelationPolicy",
    "historical_relation_candidates",
    "should_show_historical_relation",
]


_STOP_TERMS = frozenset(
    {
        "and",
        "are",
        "but",
        "for",
        "from",
        "how",
        "into",
        "the",
        "this",
        "that",
        "with",
    }
)
