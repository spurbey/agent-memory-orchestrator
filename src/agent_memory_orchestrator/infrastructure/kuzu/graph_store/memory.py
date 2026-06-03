from __future__ import annotations

import json
from typing import Any

from .helpers import _now
from .helpers import _terms
from .models import GraphEdge
from .models import GraphNode


class InMemoryGraphStore:
    """Test store. Production uses KuzuGraphStore."""

    def __init__(self) -> None:
        self.nodes: dict[str, GraphNode] = {}
        self.edges: dict[str, GraphEdge] = {}

    def init_schema(self) -> None:
        return

    def upsert_node(self, node: GraphNode) -> None:
        existing = self.nodes.get(node.id)
        now = _now()
        self.nodes[node.id] = GraphNode(
            **{
                **node.as_dict(),
                "created_at": node.created_at or (existing.created_at if existing else now),
                "updated_at": now,
            }
        )

    def upsert_edge(self, edge: GraphEdge) -> None:
        self.edges[edge.id] = GraphEdge(**{**edge.as_dict(), "created_at": edge.created_at or _now()})

    def search_nodes(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        terms = _terms(query)
        allowed = set(kinds or [])
        rows: list[tuple[float, GraphNode]] = []
        for node in self.nodes.values():
            if allowed and node.kind not in allowed:
                continue
            haystack = f"{node.kind} {node.label} {node.summary} {json.dumps(node.metadata, sort_keys=True)}".lower()
            score = sum(1.0 for term in terms if term in haystack)
            if score or not terms:
                status_bonus = 0.25 if node.status in {"active", "committed"} else 0.0
                rows.append((score + status_bonus, node))
        rows.sort(key=lambda item: item[0], reverse=True)
        return [{**node.as_dict(), "graph_score": score} for score, node in rows[:limit]]

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        allowed = set(kinds or [])
        rows = [
            node
            for node in self.nodes.values()
            if (not allowed or node.kind in allowed)
            and (not session_id or node.session_id == session_id)
            and (not status or node.status == status)
        ]
        rows.sort(key=lambda node: node.updated_at, reverse=True)
        return [node.as_dict() for node in rows[:limit]]

    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        found: list[GraphNode] = []
        for edge in self.edges.values():
            if edge.source_id == node_id and edge.target_id in self.nodes:
                found.append(self.nodes[edge.target_id])
            elif edge.target_id == node_id and edge.source_id in self.nodes:
                found.append(self.nodes[edge.source_id])
        return [node.as_dict() for node in found[:limit]]

    def list_edges(
        self,
        *,
        limit: int = 100,
        session_id: str = "",
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        allowed = set(kinds or [])
        rows: list[GraphEdge] = []
        for edge in self.edges.values():
            source = self.nodes.get(edge.source_id)
            target = self.nodes.get(edge.target_id)
            if allowed and edge.kind not in allowed:
                continue
            if session_id and not (
                (source and source.session_id == session_id) or (target and target.session_id == session_id)
            ):
                continue
            rows.append(edge)
        rows.sort(key=lambda edge: edge.created_at, reverse=True)
        return [edge.as_dict() for edge in rows[:limit]]

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            if session_id and node.session_id != session_id:
                continue
            counts[node.status] = counts.get(node.status, 0) + 1
        return {"backend": "memory", "counts": counts}

    def set_node_status(self, node_id: str, status: str) -> bool:
        node = self.nodes.get(node_id)
        if node is None:
            return False
        self.nodes[node_id] = GraphNode(**{**node.as_dict(), "status": status, "updated_at": _now()})
        return True

    def close(self) -> None:
        return
