from __future__ import annotations

import hashlib

from .identity import version_id
from .models import HarnessEdge
from .models import HarnessNode


BASELINE_SNAPSHOT_ID = "baseline:working-tree"


def add_baseline_versions(
    *,
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
    snapshot_id: str = BASELINE_SNAPSHOT_ID,
) -> None:
    """Create current-state version nodes for structural entities.

    This is not git history. It is the Phase 1 baseline snapshot that future
    commit/work-window updates can diff against.
    """

    structural_nodes = tuple(
        node for node in nodes.values() if node.kind in {"File", "Symbol", "CodeRegion"}
    )
    for node in structural_nodes:
        version_kind = f"{node.kind}Version"
        node_version_id = version_id(node.id, snapshot_id)
        nodes[node_version_id] = HarnessNode(
            id=node_version_id,
            kind=version_kind,
            label=f"{node.label}@{snapshot_id}",
            repo_id=node.repo_id,
            summary=node.summary,
            metadata={
                "entity_id": node.id,
                "entity_kind": node.kind,
                "snapshot_id": snapshot_id,
                "path": node.metadata.get("path", ""),
                "qualified_name": node.metadata.get("qualified_name", ""),
                "symbol_kind": node.metadata.get("symbol_kind", ""),
                "region_kind": node.metadata.get("region_kind", ""),
                "content_fingerprint": _fingerprint(node),
            },
        )
        edges.append(
            HarnessEdge(
                source_id=node_version_id,
                target_id=node.id,
                kind="VERSION_OF",
                metadata={"snapshot_id": snapshot_id},
            )
        )


def _fingerprint(node: HarnessNode) -> str:
    parts = [
        node.kind,
        node.label,
        node.summary,
        str(node.metadata.get("path", "")),
        str(node.metadata.get("qualified_name", "")),
        str(node.metadata.get("line_start", "")),
        str(node.metadata.get("line_end", "")),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


__all__ = ["BASELINE_SNAPSHOT_ID", "add_baseline_versions"]
