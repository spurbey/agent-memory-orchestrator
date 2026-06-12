from __future__ import annotations

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


@dataclass(slots=True, frozen=True)
class HistoricalRelationCandidate:
    edge: HarnessEdge
    anchor_id: str
    related_id: str
    related_node: HarnessNode
    occurrence_nodes: tuple[HarnessNode, ...]
    score: float


def historical_relation_candidates(
    graph: StructuralHarnessGraph,
    *,
    anchor_node_id: str,
    policy: HistoricalRelationPolicy = HistoricalRelationPolicy(),
) -> tuple[HistoricalRelationCandidate, ...]:
    """Return conservative historical relation candidates for one anchor.

    A high Jaccard score is not enough: tiny history can produce Jaccard 1.0.
    The minimum occurrence gate keeps early/noisy co-changes out of patch cards.
    """

    node_by_id = graph.node_by_id()
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
        occurrence_nodes = _occurrence_nodes_for_edge(graph, edge, policy=policy)
        out.append(
            HistoricalRelationCandidate(
                edge=edge,
                anchor_id=anchor_node_id,
                related_id=related_id,
                related_node=related,
                occurrence_nodes=occurrence_nodes,
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


def _occurrence_nodes_for_edge(
    graph: StructuralHarnessGraph,
    edge: HarnessEdge,
    *,
    policy: HistoricalRelationPolicy,
) -> tuple[HarnessNode, ...]:
    node_by_id = graph.node_by_id()
    occurrence_ids = tuple(str(item) for item in edge.metadata.get("occurrence_ids", ()))
    occurrences = [
        node
        for node_id in occurrence_ids
        if (node := node_by_id.get(node_id)) is not None and node.kind == "RelationOccurrence"
    ]
    occurrences.sort(key=lambda node: str(node.metadata.get("commit_id") or node.id), reverse=True)
    return tuple(occurrences[: max(0, policy.max_occurrences_per_card)])


def _candidate_score(edge: HarnessEdge) -> float:
    metadata = edge.metadata
    stored_strength = float(metadata.get("stored_strength") or edge.weight or 0.0)
    cochange_count = int(metadata.get("cochange_count") or 0)
    # Strength is primary; count only breaks ties among equally strong edges.
    return round(stored_strength + min(0.05, cochange_count * 0.005), 4)


__all__ = [
    "HistoricalRelationCandidate",
    "HistoricalRelationPolicy",
    "historical_relation_candidates",
    "should_show_historical_relation",
]
