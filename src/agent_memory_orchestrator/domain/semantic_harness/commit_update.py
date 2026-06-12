from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .hunk_mapping import CommitHunk
from .hunk_mapping import HunkEntityMapping
from .hunk_mapping import map_hunk_to_entities
from .identity import commit_id
from .identity import file_id
from .identity import hunk_id
from .identity import normalize_file_path
from .identity import version_id
from .identity import work_window_id
from .models import HarnessEdge
from .models import HarnessNode
from .models import StructuralHarnessGraph
from .relations import build_cochange_seed


@dataclass(slots=True, frozen=True)
class CommitWorkWindow:
    repo_id: str
    session_id: str
    commit_sha: str
    commit_message: str
    hunks: tuple[CommitHunk, ...]
    work_window_id: str = ""

    @property
    def resolved_work_window_id(self) -> str:
        return self.work_window_id or work_window_id(self.repo_id, self.session_id, self.commit_sha)

    @property
    def resolved_commit_id(self) -> str:
        return commit_id(self.repo_id, self.commit_sha)

    def as_dict(self) -> dict[str, Any]:
        return {
            "work_window_id": self.resolved_work_window_id,
            "repo_id": self.repo_id,
            "session_id": self.session_id,
            "commit": {"sha": self.commit_sha, "message": self.commit_message},
            "hunks": [hunk.as_dict() for hunk in self.hunks],
        }


@dataclass(slots=True, frozen=True)
class GraphUpdateDelta:
    delta_id: str
    repo_id: str
    work_window_id: str
    commit_id: str
    created_nodes: tuple[HarnessNode, ...]
    created_edges: tuple[HarnessEdge, ...]
    hunk_mappings: tuple[HunkEntityMapping, ...]
    semantic_review: dict[str, int]
    updated_edge_weights: tuple[dict[str, Any], ...] = ()
    created_relation_occurrences: tuple[HarnessNode, ...] = ()
    projection_refresh_required: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "delta_id": self.delta_id,
            "repo_id": self.repo_id,
            "work_window_id": self.work_window_id,
            "commit_id": self.commit_id,
            "created_nodes": [node.as_dict() for node in self.created_nodes],
            "created_edges": [edge.as_dict() for edge in self.created_edges],
            "updated_edge_weights": list(self.updated_edge_weights),
            "created_relation_occurrences": [node.as_dict() for node in self.created_relation_occurrences],
            "hunk_mappings": [mapping.as_dict() for mapping in self.hunk_mappings],
            "semantic_review": self.semantic_review,
            "projection_refresh_required": self.projection_refresh_required,
        }


def build_commit_update_delta(graph: StructuralHarnessGraph, window: CommitWorkWindow) -> GraphUpdateDelta:
    """Build deterministic update nodes/edges for one commit work window.

    The delta is idempotent and non-mutating. Applying it to storage is a later
    application/infrastructure concern.
    """

    commit_node_id = window.resolved_commit_id
    work_node_id = window.resolved_work_window_id
    nodes: dict[str, HarnessNode] = {
        work_node_id: HarnessNode(
            id=work_node_id,
            kind="WorkWindow",
            label=f"{window.session_id}:{window.commit_sha[:12]}",
            repo_id=window.repo_id,
            summary=window.commit_message,
            metadata={"session_id": window.session_id, "commit_sha": window.commit_sha},
        ),
        commit_node_id: HarnessNode(
            id=commit_node_id,
            kind="Commit",
            label=window.commit_sha[:12],
            repo_id=window.repo_id,
            summary=window.commit_message,
            metadata={"sha": window.commit_sha, "message": window.commit_message},
        ),
    }
    edges: list[HarnessEdge] = [
        HarnessEdge(source_id=commit_node_id, target_id=work_node_id, kind="DERIVED_FROM_WORK_WINDOW"),
    ]
    mappings: list[HunkEntityMapping] = []
    for raw_hunk in window.hunks:
        hunk = _resolve_hunk_id(window, raw_hunk)
        path = normalize_file_path(hunk.file_path)
        hunk_node_id = hunk.hunk_id
        nodes[hunk_node_id] = HarnessNode(
            id=hunk_node_id,
            kind="Hunk",
            label=f"{path}:{hunk.new_range.start}",
            repo_id=window.repo_id,
            summary=hunk.text[:160],
            metadata={
                "path": path,
                "commit_sha": window.commit_sha,
                "old_range": hunk.old_range.as_dict(),
                "new_range": hunk.new_range.as_dict(),
            },
        )
        edges.append(HarnessEdge(source_id=hunk_node_id, target_id=work_node_id, kind="DERIVED_FROM_WORK_WINDOW"))
        _add_file_version(window=window, path=path, nodes=nodes, edges=edges)
        for mapping in map_hunk_to_entities(graph, hunk):
            mappings.append(mapping)
            if mapping.target_node_id:
                edges.append(
                    HarnessEdge(
                        source_id=hunk_node_id,
                        target_id=mapping.target_node_id,
                        kind=mapping.edge_kind,
                        confidence=mapping.confidence,
                        metadata={"mapping_status": mapping.status, "reason": mapping.reason},
                    )
                )
            if mapping.status == "mapped" and mapping.target_kind in {"Symbol", "CodeRegion"}:
                _add_entity_version(window=window, mapping=mapping, graph=graph, nodes=nodes, edges=edges)
    cochange = build_cochange_seed(
        repo_id=window.repo_id,
        work_window_id=work_node_id,
        commit_id=commit_node_id,
        commit_message=window.commit_message,
        mappings=tuple(mappings),
    )
    for occurrence in cochange.occurrence_nodes:
        nodes[occurrence.id] = occurrence
    edges.extend(cochange.edges)
    return GraphUpdateDelta(
        delta_id=_delta_id(work_node_id, commit_node_id),
        repo_id=window.repo_id,
        work_window_id=work_node_id,
        commit_id=commit_node_id,
        created_nodes=tuple(nodes.values()),
        created_edges=_dedupe_edges(edges),
        hunk_mappings=tuple(mappings),
        semantic_review={"accepted": 0, "review_only": 0, "rejected": 0, "quarantined": 0},
        updated_edge_weights=cochange.weight_updates,
        created_relation_occurrences=cochange.occurrence_nodes,
        projection_refresh_required=True,
    )


def _add_file_version(
    *,
    window: CommitWorkWindow,
    path: str,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    entity_id = file_id(window.repo_id, path)
    node_id = version_id(entity_id, window.commit_sha)
    if node_id not in nodes:
        nodes[node_id] = HarnessNode(
            id=node_id,
            kind="FileVersion",
            label=f"{path}@{window.commit_sha[:12]}",
            repo_id=window.repo_id,
            metadata={"entity_id": entity_id, "entity_kind": "File", "path": path, "commit_sha": window.commit_sha},
        )
    edges.append(HarnessEdge(source_id=node_id, target_id=entity_id, kind="VERSION_OF", metadata={"commit_sha": window.commit_sha}))
    edges.append(HarnessEdge(source_id=node_id, target_id=window.resolved_commit_id, kind="CHANGED_IN", metadata={"commit_sha": window.commit_sha}))


def _add_entity_version(
    *,
    window: CommitWorkWindow,
    mapping: HunkEntityMapping,
    graph: StructuralHarnessGraph,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    source = graph.node_by_id().get(mapping.target_node_id)
    if source is None:
        return
    node_id = version_id(source.id, window.commit_sha)
    version_kind = f"{source.kind}Version"
    nodes[node_id] = HarnessNode(
        id=node_id,
        kind=version_kind,
        label=f"{source.label}@{window.commit_sha[:12]}",
        repo_id=window.repo_id,
        summary=source.summary,
        metadata={
            "entity_id": source.id,
            "entity_kind": source.kind,
            "path": source.metadata.get("path", ""),
            "qualified_name": source.metadata.get("qualified_name", ""),
            "symbol_kind": source.metadata.get("symbol_kind", ""),
            "region_kind": source.metadata.get("region_kind", ""),
            "commit_sha": window.commit_sha,
            "mapping_confidence": mapping.confidence,
        },
    )
    edges.append(HarnessEdge(source_id=node_id, target_id=source.id, kind="VERSION_OF", metadata={"commit_sha": window.commit_sha}))
    edges.append(HarnessEdge(source_id=node_id, target_id=window.resolved_commit_id, kind="CHANGED_IN", metadata={"commit_sha": window.commit_sha}))


def _resolve_hunk_id(window: CommitWorkWindow, raw_hunk: CommitHunk) -> CommitHunk:
    if raw_hunk.hunk_id:
        return raw_hunk
    return CommitHunk(
        hunk_id=hunk_id(
            window.repo_id,
            window.commit_sha,
            raw_hunk.file_path,
            raw_hunk.old_range.start,
            raw_hunk.new_range.start,
        ),
        file_path=raw_hunk.file_path,
        old_range=raw_hunk.old_range,
        new_range=raw_hunk.new_range,
        text=raw_hunk.text,
    )


def _dedupe_edges(edges: list[HarnessEdge]) -> tuple[HarnessEdge, ...]:
    seen: set[tuple[str, str, str]] = set()
    out: list[HarnessEdge] = []
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return tuple(out)


def _delta_id(work_node_id: str, commit_node_id: str) -> str:
    stable = f"{work_node_id}|{commit_node_id}"
    return f"delta:{hashlib.sha256(stable.encode('utf-8')).hexdigest()[:24]}"


__all__ = ["CommitWorkWindow", "GraphUpdateDelta", "build_commit_update_delta"]
