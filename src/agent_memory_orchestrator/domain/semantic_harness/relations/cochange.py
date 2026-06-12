from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..hunk_mapping import HunkEntityMapping
from ..models import HarnessEdge
from ..models import HarnessNode


@dataclass(slots=True, frozen=True)
class CoChangeSeed:
    occurrence_nodes: tuple[HarnessNode, ...]
    edges: tuple[HarnessEdge, ...]
    weight_updates: tuple[dict[str, object], ...]


def build_cochange_seed(
    *,
    repo_id: str,
    work_window_id: str,
    commit_id: str,
    mappings: tuple[HunkEntityMapping, ...],
) -> CoChangeSeed:
    """Create deterministic co-change relation evidence for one commit delta.

    This creates occurrence evidence with an empty semantic reason. Later Qwen
    enrichment can attach reasons, but the fact that two entities changed in
    the same work window is deterministic.
    """

    changed = _changed_entities(mappings)
    if len(changed) < 2:
        return CoChangeSeed(occurrence_nodes=(), edges=(), weight_updates=())
    occurrence_nodes: list[HarnessNode] = []
    edges: list[HarnessEdge] = []
    weight_updates: list[dict[str, object]] = []
    for left_index, source_id in enumerate(changed):
        for target_id in changed[left_index + 1 :]:
            occurrence_id = _occurrence_id(repo_id, work_window_id, commit_id, source_id, target_id)
            occurrence_nodes.append(
                HarnessNode(
                    id=occurrence_id,
                    kind="RelationOccurrence",
                    label="co-change",
                    repo_id=repo_id,
                    summary="Deterministic co-change occurrence; semantic reason pending.",
                    metadata={
                        "relation_kind": "CO_CHANGED_WITH",
                        "source_id": source_id,
                        "target_id": target_id,
                        "work_window_id": work_window_id,
                        "commit_id": commit_id,
                        "reason": "",
                        "reason_status": "semantic_pending",
                        "confidence": _pair_confidence(source_id, target_id, mappings),
                    },
                )
            )
            edges.append(
                HarnessEdge(
                    source_id=source_id,
                    target_id=target_id,
                    kind="CO_CHANGED_WITH",
                    weight=1.0,
                    confidence=_pair_confidence(source_id, target_id, mappings),
                    metadata={
                        "cochange_count_delta": 1,
                        "occurrence_id": occurrence_id,
                        "work_window_id": work_window_id,
                        "commit_id": commit_id,
                    },
                )
            )
            edges.append(
                HarnessEdge(
                    source_id=occurrence_id,
                    target_id=work_window_id,
                    kind="DERIVED_FROM_WORK_WINDOW",
                    metadata={"commit_id": commit_id},
                )
            )
            weight_updates.append(
                {
                    "source_id": source_id,
                    "target_id": target_id,
                    "kind": "CO_CHANGED_WITH",
                    "cochange_count_delta": 1,
                    "occurrence_id": occurrence_id,
                }
            )
    return CoChangeSeed(
        occurrence_nodes=tuple(occurrence_nodes),
        edges=tuple(edges),
        weight_updates=tuple(weight_updates),
    )


def _changed_entities(mappings: tuple[HunkEntityMapping, ...]) -> tuple[str, ...]:
    accepted = {
        mapping.target_node_id
        for mapping in mappings
        if mapping.target_node_id and mapping.status in {"mapped", "file_level"}
    }
    return tuple(sorted(accepted))


def _pair_confidence(source_id: str, target_id: str, mappings: tuple[HunkEntityMapping, ...]) -> float:
    confidences = [
        mapping.confidence
        for mapping in mappings
        if mapping.target_node_id in {source_id, target_id} and mapping.status in {"mapped", "file_level"}
    ]
    if not confidences:
        return 0.0
    return round(min(confidences), 2)


def _occurrence_id(repo_id: str, work_window_id: str, commit_id: str, source_id: str, target_id: str) -> str:
    left, right = sorted((source_id, target_id))
    stable = "|".join([repo_id, work_window_id, commit_id, left, right, "CO_CHANGED_WITH"])
    return f"relation_occurrence:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"


__all__ = ["CoChangeSeed", "build_cochange_seed"]
