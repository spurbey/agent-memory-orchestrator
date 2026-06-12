from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .identity import normalize_file_path
from .models import HarnessNode
from .models import StructuralHarnessGraph


@dataclass(slots=True, frozen=True)
class HunkRange:
    start: int
    count: int

    @property
    def end(self) -> int:
        return self.start + max(0, self.count) - 1

    @property
    def is_empty(self) -> bool:
        return self.count <= 0

    def as_dict(self) -> dict[str, int]:
        return {"start": self.start, "count": self.count, "end": self.end}


@dataclass(slots=True, frozen=True)
class CommitHunk:
    file_path: str
    old_range: HunkRange
    new_range: HunkRange
    text: str = ""
    hunk_id: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "hunk_id": self.hunk_id,
            "file_path": normalize_file_path(self.file_path),
            "old_range": self.old_range.as_dict(),
            "new_range": self.new_range.as_dict(),
            "text": self.text,
        }


@dataclass(slots=True, frozen=True)
class HunkEntityMapping:
    hunk_id: str
    file_path: str
    target_node_id: str
    target_kind: str
    edge_kind: str
    confidence: float
    status: str
    reason: str
    score_parts: dict[str, float]

    def as_dict(self) -> dict[str, Any]:
        return {
            "hunk_id": self.hunk_id,
            "file_path": self.file_path,
            "target_node_id": self.target_node_id,
            "target_kind": self.target_kind,
            "edge_kind": self.edge_kind,
            "confidence": self.confidence,
            "status": self.status,
            "reason": self.reason,
            "score_parts": self.score_parts,
        }


def map_hunk_to_entities(graph: StructuralHarnessGraph, hunk: CommitHunk) -> tuple[HunkEntityMapping, ...]:
    """Map a changed hunk to product graph entities with confidence.

    This is the first deterministic commit-update primitive. It does not mutate
    graph truth. It emits mapping candidates that later update stages can turn
    into version nodes and `MAPS_TO_*` edges.
    """

    path = normalize_file_path(hunk.file_path)
    file_node = _file_node_for_path(graph, path)
    if file_node is None:
        return (
            HunkEntityMapping(
                hunk_id=hunk.hunk_id,
                file_path=path,
                target_node_id="",
                target_kind="",
                edge_kind="",
                confidence=0.0,
                status="unresolved",
                reason="file_not_in_graph",
                score_parts={},
            ),
        )

    candidates = tuple(
        node
        for node in graph.nodes
        if node.kind in {"Symbol", "CodeRegion"}
        and normalize_file_path(str(node.metadata.get("path", ""))) == path
        and _has_span(node)
    )
    overlaps = tuple(
        (node, _score_candidate(node, hunk))
        for node in candidates
        if _range_overlaps_node(hunk.old_range, node) or _range_overlaps_node(hunk.new_range, node)
    )
    if not overlaps:
        return (
            _file_level_mapping(
                hunk=hunk,
                file_path=path,
                file_node=file_node,
                reason="no_symbol_or_code_region_overlap",
            ),
        )

    competing_count = len(overlaps)
    mappings: list[HunkEntityMapping] = []
    for node, score_parts in sorted(
        overlaps,
        key=lambda item: (-_score_total(item[1], competing_count=competing_count), item[0].kind, item[0].label),
    ):
        confidence = _score_total(score_parts, competing_count=competing_count)
        status = "mapped" if confidence >= 0.75 and competing_count == 1 else "review_only"
        mappings.append(
            HunkEntityMapping(
                hunk_id=hunk.hunk_id,
                file_path=path,
                target_node_id=node.id,
                target_kind=node.kind,
                edge_kind="MAPS_TO_SYMBOL" if node.kind == "Symbol" else "MAPS_TO_CODE_REGION",
                confidence=confidence,
                status=status,
                reason=_mapping_reason(node=node, status=status, competing_count=competing_count),
                score_parts=score_parts,
            )
        )
    return tuple(mappings)


def _score_candidate(node: HarnessNode, hunk: CommitHunk) -> dict[str, float]:
    old_overlap = _range_overlaps_node(hunk.old_range, node)
    new_overlap = _range_overlaps_node(hunk.new_range, node)
    full_containment = _range_inside_node(hunk.old_range, node) or _range_inside_node(hunk.new_range, node)
    old_new_agreement = old_overlap and new_overlap
    if hunk.old_range.is_empty or hunk.new_range.is_empty:
        old_new_agreement = old_overlap or new_overlap
    return {
        "base": 0.50,
        "span_containment": 0.25 if full_containment else 0.10,
        "old_new_agreement": 0.15 if old_new_agreement else 0.0,
        "signature_stability": 0.05 if node.kind == "Symbol" and node.metadata.get("symbol_kind") else 0.0,
    }


def _score_total(score_parts: dict[str, float], *, competing_count: int) -> float:
    total = sum(score_parts.values())
    total += 0.05 if competing_count == 1 else 0.0
    return round(min(total, 0.95), 2)


def _range_overlaps_node(line_range: HunkRange, node: HarnessNode) -> bool:
    if line_range.is_empty:
        return False
    line_start, line_end = _node_span(node)
    return line_range.start <= line_end and line_range.end >= line_start


def _range_inside_node(line_range: HunkRange, node: HarnessNode) -> bool:
    if line_range.is_empty:
        return False
    line_start, line_end = _node_span(node)
    return line_range.start >= line_start and line_range.end <= line_end


def _file_node_for_path(graph: StructuralHarnessGraph, path: str) -> HarnessNode | None:
    for node in graph.nodes:
        if node.kind == "File" and normalize_file_path(str(node.metadata.get("path", ""))) == path:
            return node
    return None


def _has_span(node: HarnessNode) -> bool:
    return bool(node.metadata.get("line_start")) and bool(node.metadata.get("line_end"))


def _node_span(node: HarnessNode) -> tuple[int, int]:
    return int(node.metadata.get("line_start") or 0), int(node.metadata.get("line_end") or 0)


def _file_level_mapping(
    *,
    hunk: CommitHunk,
    file_path: str,
    file_node: HarnessNode,
    reason: str,
) -> HunkEntityMapping:
    return HunkEntityMapping(
        hunk_id=hunk.hunk_id,
        file_path=file_path,
        target_node_id=file_node.id,
        target_kind="File",
        edge_kind="CHANGED_IN",
        confidence=0.55,
        status="file_level",
        reason=reason,
        score_parts={"base": 0.50, "file_fallback": 0.05},
    )


def _mapping_reason(*, node: HarnessNode, status: str, competing_count: int) -> str:
    if status == "mapped":
        return f"hunk range is grounded to one {node.kind} span with no competing overlap"
    return f"hunk overlaps {competing_count} entities; keep {node.kind} mapping review-only"


__all__ = [
    "CommitHunk",
    "HunkEntityMapping",
    "HunkRange",
    "map_hunk_to_entities",
]
