from __future__ import annotations

import json
import gc
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


class GraphBackendUnavailable(RuntimeError):
    pass


@dataclass(slots=True, frozen=True)
class GraphNode:
    id: str
    kind: str
    label: str
    summary: str = ""
    status: str = "draft"
    scope: str = "session"
    session_id: str = ""
    project_id: str = "default"
    source_app: str = "unknown"
    evidence_id: str = ""
    commit_id: str = ""
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "summary": self.summary,
            "status": self.status,
            "scope": self.scope,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "source_app": self.source_app,
            "evidence_id": self.evidence_id,
            "commit_id": self.commit_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class GraphEdge:
    id: str
    source_id: str
    target_id: str
    kind: str
    weight: float = 1.0
    confidence: float = 0.8
    evidence_id: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "weight": self.weight,
            "confidence": self.confidence,
            "evidence_id": self.evidence_id,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


class GraphStore(Protocol):
    def init_schema(self) -> None:
        """Create graph schema if missing."""

    def upsert_node(self, node: GraphNode) -> None:
        """Create or update one graph node."""

    def upsert_edge(self, edge: GraphEdge) -> None:
        """Create or replace one graph edge."""

    def search_nodes(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        """Search graph nodes by text."""

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        """List graph nodes with simple filters."""

    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        """Return adjacent nodes."""

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        """Return merge/graph status."""

    def close(self) -> None:
        """Close backend resources."""


class KuzuGraphStore:
    """Embedded Kuzu graph backend.

    The first implementation uses a generic node/edge schema. Node `kind`
    values carry the richer graph model while still using a real graph database
    for traversals.
    """

    def __init__(self, graph_path: Path) -> None:
        try:
            import kuzu  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional local install
            raise GraphBackendUnavailable(
                "kuzu_unavailable: install the Kuzu Python package in this environment"
            ) from exc
        self.graph_path = graph_path
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(str(graph_path))
        self._conn = kuzu.Connection(self._db)

    def init_schema(self) -> None:
        self._conn.execute(
            """
            CREATE NODE TABLE IF NOT EXISTS GraphNode(
                id STRING,
                kind STRING,
                label STRING,
                summary STRING,
                status STRING,
                scope STRING,
                session_id STRING,
                project_id STRING,
                source_app STRING,
                evidence_id STRING,
                commit_id STRING,
                created_at STRING,
                updated_at STRING,
                metadata_json STRING,
                PRIMARY KEY(id)
            )
            """
        )
        self._conn.execute(
            """
            CREATE REL TABLE IF NOT EXISTS GraphEdge(
                FROM GraphNode TO GraphNode,
                id STRING,
                kind STRING,
                weight DOUBLE,
                confidence DOUBLE,
                evidence_id STRING,
                created_at STRING,
                metadata_json STRING
            )
            """
        )

    def upsert_node(self, node: GraphNode) -> None:
        now = _now()
        created_at = node.created_at or now
        updated = GraphNode(**{**node.as_dict(), "created_at": created_at, "updated_at": now})
        props = _node_props(updated)
        try:
            self._conn.execute(f"CREATE (:GraphNode {{{props}}})")
        except Exception:
            self._conn.execute(
                "MATCH (n:GraphNode) "
                f"WHERE n.id = {_q(updated.id)} "
                "SET "
                f"n.kind = {_q(updated.kind)}, "
                f"n.label = {_q(updated.label)}, "
                f"n.summary = {_q(updated.summary)}, "
                f"n.status = {_q(updated.status)}, "
                f"n.scope = {_q(updated.scope)}, "
                f"n.session_id = {_q(updated.session_id)}, "
                f"n.project_id = {_q(updated.project_id)}, "
                f"n.source_app = {_q(updated.source_app)}, "
                f"n.evidence_id = {_q(updated.evidence_id)}, "
                f"n.commit_id = {_q(updated.commit_id)}, "
                f"n.updated_at = {_q(updated.updated_at)}, "
                f"n.metadata_json = {_q(json.dumps(updated.metadata, sort_keys=True))}"
            )

    def upsert_edge(self, edge: GraphEdge) -> None:
        created_at = edge.created_at or _now()
        self._conn.execute(f"MATCH (a:GraphNode)-[e:GraphEdge]->(b:GraphNode) WHERE e.id = {_q(edge.id)} DELETE e")
        self._conn.execute(
            "MATCH (a:GraphNode), (b:GraphNode) "
            f"WHERE a.id = {_q(edge.source_id)} AND b.id = {_q(edge.target_id)} "
            "CREATE (a)-[:GraphEdge {"
            f"id: {_q(edge.id)}, kind: {_q(edge.kind)}, weight: {float(edge.weight)}, "
            f"confidence: {float(edge.confidence)}, evidence_id: {_q(edge.evidence_id)}, "
            f"created_at: {_q(created_at)}, metadata_json: {_q(json.dumps(edge.metadata, sort_keys=True))}"
            "}]->(b)"
        )

    def search_nodes(self, query: str, *, limit: int = 25, kinds: list[str] | None = None) -> list[dict[str, Any]]:
        where = _search_where(query, kinds)
        result = self._conn.execute(
            "MATCH (n:GraphNode) "
            f"{where} "
            "RETURN n.id, n.kind, n.label, n.summary, n.status, n.scope, n.session_id, "
            "n.project_id, n.source_app, n.evidence_id, n.commit_id, n.created_at, n.updated_at, n.metadata_json "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_node(row) for row in _rows(result)]

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if kinds:
            clauses.append("(" + " OR ".join(f"n.kind = {_q(kind)}" for kind in kinds) + ")")
        if session_id:
            clauses.append(f"n.session_id = {_q(session_id)}")
        if status:
            clauses.append(f"n.status = {_q(status)}")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        result = self._conn.execute(
            "MATCH (n:GraphNode) "
            f"{where} "
            "RETURN n.id, n.kind, n.label, n.summary, n.status, n.scope, n.session_id, "
            "n.project_id, n.source_app, n.evidence_id, n.commit_id, n.created_at, n.updated_at, n.metadata_json "
            "ORDER BY n.updated_at DESC "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_node(row) for row in _rows(result)]

    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        result = self._conn.execute(
            "MATCH (n:GraphNode)-[e:GraphEdge]-(m:GraphNode) "
            f"WHERE n.id = {_q(node_id)} "
            "RETURN m.id, m.kind, m.label, m.summary, m.status, m.scope, m.session_id, "
            "m.project_id, m.source_app, m.evidence_id, m.commit_id, m.created_at, m.updated_at, m.metadata_json "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_node(row) for row in _rows(result)]

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        where = f"WHERE n.session_id = {_q(session_id)} " if session_id else ""
        result = self._conn.execute(f"MATCH (n:GraphNode) {where}RETURN n.status, count(*)")
        counts: dict[str, int] = {}
        for row in _rows(result):
            counts[str(row[0])] = int(row[1])
        return {"backend": "kuzu", "graph_path": str(self.graph_path), "counts": counts}

    def close(self) -> None:
        for attr in ("_conn", "_db"):
            obj = getattr(self, attr, None)
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
            setattr(self, attr, None)
        gc.collect()


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

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        counts: dict[str, int] = {}
        for node in self.nodes.values():
            if session_id and node.session_id != session_id:
                continue
            counts[node.status] = counts.get(node.status, 0) + 1
        return {"backend": "memory", "counts": counts}

    def close(self) -> None:
        return


def _node_props(node: GraphNode) -> str:
    return ", ".join(
        [
            f"id: {_q(node.id)}",
            f"kind: {_q(node.kind)}",
            f"label: {_q(node.label)}",
            f"summary: {_q(node.summary)}",
            f"status: {_q(node.status)}",
            f"scope: {_q(node.scope)}",
            f"session_id: {_q(node.session_id)}",
            f"project_id: {_q(node.project_id)}",
            f"source_app: {_q(node.source_app)}",
            f"evidence_id: {_q(node.evidence_id)}",
            f"commit_id: {_q(node.commit_id)}",
            f"created_at: {_q(node.created_at)}",
            f"updated_at: {_q(node.updated_at)}",
            f"metadata_json: {_q(json.dumps(node.metadata, sort_keys=True))}",
        ]
    )


def _search_where(query: str, kinds: list[str] | None) -> str:
    clauses: list[str] = []
    terms = _terms(query)
    if terms:
        clauses.append(
            "("
            + " OR ".join(
                f"lower(n.label) CONTAINS {_q(term)} OR lower(n.summary) CONTAINS {_q(term)} "
                f"OR lower(n.metadata_json) CONTAINS {_q(term)}"
                for term in terms[:8]
            )
            + ")"
        )
    if kinds:
        clauses.append("(" + " OR ".join(f"n.kind = {_q(kind)}" for kind in kinds) + ")")
    return "WHERE " + " AND ".join(clauses) if clauses else ""


def _rows(result: Any) -> list[list[Any]]:
    rows: list[list[Any]] = []
    if hasattr(result, "has_next") and hasattr(result, "get_next"):
        while result.has_next():
            rows.append(result.get_next())
        return rows
    if isinstance(result, list):
        return result
    return rows


def _row_to_node(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[13] if len(row) > 13 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row[0],
        "kind": row[1],
        "label": row[2],
        "summary": row[3],
        "status": row[4],
        "scope": row[5],
        "session_id": row[6],
        "project_id": row[7],
        "source_app": row[8],
        "evidence_id": row[9],
        "commit_id": row[10],
        "created_at": row[11],
        "updated_at": row[12],
        "metadata": metadata,
    }


def _q(value: object) -> str:
    return json.dumps("" if value is None else str(value))


def _terms(text: str) -> list[str]:
    return [
        re.sub(r"[^a-z0-9_.-]+", "", token.lower())
        for token in str(text or "").split()
        if re.sub(r"[^a-z0-9_.-]+", "", token.lower())
    ]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
