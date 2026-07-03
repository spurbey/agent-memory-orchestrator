from __future__ import annotations

import json
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness.models import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness.models import HarnessNode

NODE_PROPERTIES = (
    "node_id",
    "kind",
    "label",
    "repo_id",
    "status",
    "summary",
    "path",
    "qualified_name",
    "line_start",
    "line_end",
    "metadata_json",
)
EDGE_PROPERTIES = ("source_id", "target_id", "kind", "repo_id", "weight", "confidence", "metadata_json")


def node_properties(node: HarnessNode) -> dict[str, Any]:
    metadata = node.metadata
    return {
        "node_id": node.id,
        "kind": node.kind,
        "label": node.label,
        "repo_id": node.repo_id,
        "status": node.status,
        "summary": node.summary,
        "path": str(metadata.get("path") or ""),
        "qualified_name": str(metadata.get("qualified_name") or ""),
        "line_start": int(metadata.get("line_start") or 0),
        "line_end": int(metadata.get("line_end") or 0),
        "metadata_json": json_dumps(metadata),
    }


def edge_properties(edge: HarnessEdge, *, repo_id: str) -> dict[str, Any]:
    return {
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "kind": edge.kind,
        "repo_id": repo_id,
        "weight": float(edge.weight),
        "confidence": float(edge.confidence),
        "metadata_json": json_dumps(edge.metadata),
    }


def node_from_properties(row: dict[str, Any]) -> HarnessNode:
    metadata = json_loads(row.get("metadata_json"))
    for key in ("path", "qualified_name"):
        if value := str(row.get(key) or ""):
            metadata.setdefault(key, value)
    for key in ("line_start", "line_end"):
        if value := int(row.get(key) or 0):
            metadata.setdefault(key, value)
    return HarnessNode(
        id=str(row.get("node_id") or ""),
        kind=str(row.get("kind") or row.get("$label") or ""),
        label=str(row.get("label") or ""),
        repo_id=str(row.get("repo_id") or ""),
        status=str(row.get("status") or "active"),
        summary=str(row.get("summary") or ""),
        metadata=metadata,
    )


def edge_from_properties(row: dict[str, Any], *, kind: str = "") -> HarnessEdge:
    return HarnessEdge(
        source_id=str(row.get("source_id") or ""),
        target_id=str(row.get("target_id") or ""),
        kind=str(row.get("kind") or kind or row.get("$label") or ""),
        weight=float(row.get("weight") or 1.0),
        confidence=float(row.get("confidence") or 1.0),
        metadata=json_loads(row.get("metadata_json")),
    )


def result_properties(result: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = result.get(name)
    if not isinstance(value, dict):
        return []
    rows = value.get("properties")
    return [dict(row) for row in rows] if isinstance(rows, list) else []


def json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def json_loads(value: Any) -> dict[str, Any]:
    try:
        loaded = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


__all__ = [
    "EDGE_PROPERTIES",
    "NODE_PROPERTIES",
    "edge_from_properties",
    "edge_properties",
    "json_dumps",
    "json_loads",
    "node_from_properties",
    "node_properties",
    "result_properties",
]
