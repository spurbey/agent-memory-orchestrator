from __future__ import annotations

import json
import gc
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


SEARCH_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "but",
    "can",
    "could",
    "did",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "into",
    "its",
    "not",
    "now",
    "only",
    "should",
    "that",
    "the",
    "then",
    "this",
    "use",
    "used",
    "using",
    "via",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "why",
    "will",
    "with",
    "would",
}


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

    def list_edges(
        self,
        *,
        limit: int = 100,
        session_id: str = "",
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """List graph edges with simple filters."""

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        """Return merge/graph status."""

    def set_node_status(self, node_id: str, status: str) -> bool:
        """Update a node status."""

    def close(self) -> None:
        """Close backend resources."""


class KuzuGraphStore:
    """Embedded Kuzu graph backend.

    The first implementation uses a generic node/edge schema. Node `kind`
    values carry the richer graph model while still using a real graph database
    for traversals.
    """

    def __init__(self, graph_path: Path, *, read_only: bool = False) -> None:
        try:
            import kuzu  # type: ignore
        except Exception as exc:  # pragma: no cover - depends on optional local install
            raise GraphBackendUnavailable(
                "kuzu_unavailable: install the Kuzu Python package in this environment"
            ) from exc
        self.graph_path = graph_path
        self.read_only = bool(read_only)
        self.graph_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = kuzu.Database(
            str(graph_path),
            read_only=self.read_only,
            buffer_pool_size=_kuzu_buffer_pool_size(),
            max_num_threads=_kuzu_max_threads(),
        )
        self._conn = kuzu.Connection(self._db)

    def init_schema(self) -> None:
        self._ensure_writable("init_schema")
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
        self._ensure_writable("upsert_node")
        now = _now()
        created_at = node.created_at or now
        updated = GraphNode(**{**node.as_dict(), "created_at": created_at, "updated_at": now})
        if self._uses_compact_node_schema():
            self._upsert_compact_node(updated)
            return
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
        self._ensure_writable("upsert_edge")
        created_at = edge.created_at or _now()
        if self._uses_compact_node_schema():
            self._upsert_compact_edge(edge, created_at=created_at)
            return
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
        if self._uses_compact_node_schema():
            where = _compact_search_where(query, kinds)
            result = self._conn.execute(
                "MATCH (n:GraphNode) "
                f"{where} "
                "RETURN n.id, n.kind, n.packet_id, n.commit_sha, n.label, n.summary, n.properties_json "
                f"LIMIT {int(limit)}"
            )
            return [_compact_row_to_node(row) for row in _rows(result)]
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
        if self._uses_compact_node_schema():
            clauses: list[str] = []
            if kinds:
                clauses.append("(" + " OR ".join(f"n.kind = {_q(kind)}" for kind in kinds) + ")")
            if session_id:
                clauses.append(f"lower(n.properties_json) CONTAINS {_q(session_id.lower())}")
            if status:
                clauses.append(f"lower(n.properties_json) CONTAINS {_q(status.lower())}")
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            result = self._conn.execute(
                "MATCH (n:GraphNode) "
                f"{where} "
                "RETURN n.id, n.kind, n.packet_id, n.commit_sha, n.label, n.summary, n.properties_json "
                f"LIMIT {int(limit)}"
            )
            return [_compact_row_to_node(row) for row in _rows(result)]
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
        if self._uses_compact_node_schema():
            result = self._conn.execute(
                "MATCH (n:GraphNode)-[e:GraphEdge]-(m:GraphNode) "
                f"WHERE n.id = {_q(node_id)} "
                "RETURN m.id, m.kind, m.packet_id, m.commit_sha, m.label, m.summary, m.properties_json "
                f"LIMIT {int(limit)}"
            )
            return [_compact_row_to_node(row) for row in _rows(result)]
        result = self._conn.execute(
            "MATCH (n:GraphNode)-[e:GraphEdge]-(m:GraphNode) "
            f"WHERE n.id = {_q(node_id)} "
            "RETURN m.id, m.kind, m.label, m.summary, m.status, m.scope, m.session_id, "
            "m.project_id, m.source_app, m.evidence_id, m.commit_id, m.created_at, m.updated_at, m.metadata_json "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_node(row) for row in _rows(result)]

    def list_edges(
        self,
        *,
        limit: int = 100,
        session_id: str = "",
        kinds: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        if self._uses_compact_node_schema():
            clauses: list[str] = []
            if session_id:
                clauses.append(
                    f"(lower(a.properties_json) CONTAINS {_q(session_id.lower())} "
                    f"OR lower(b.properties_json) CONTAINS {_q(session_id.lower())})"
                )
            if kinds:
                clauses.append("(" + " OR ".join(f"e.kind = {_q(kind)}" for kind in kinds) + ")")
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            result = self._conn.execute(
                "MATCH (a:GraphNode)-[e:GraphEdge]->(b:GraphNode) "
                f"{where} "
                "RETURN a.id, b.id, e.kind, e.properties_json "
                f"LIMIT {int(limit)}"
            )
            return [_compact_row_to_edge(row) for row in _rows(result)]
        clauses: list[str] = []
        if session_id:
            clauses.append(f"(a.session_id = {_q(session_id)} OR b.session_id = {_q(session_id)})")
        if kinds:
            clauses.append("(" + " OR ".join(f"e.kind = {_q(kind)}" for kind in kinds) + ")")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        result = self._conn.execute(
            "MATCH (a:GraphNode)-[e:GraphEdge]->(b:GraphNode) "
            f"{where} "
            "RETURN e.id, a.id, b.id, e.kind, e.weight, e.confidence, e.evidence_id, e.created_at, e.metadata_json "
            f"LIMIT {int(limit)}"
        )
        return [_row_to_edge(row) for row in _rows(result)]

    def merge_status(self, *, session_id: str = "") -> dict[str, Any]:
        if self._uses_compact_node_schema():
            where = f"WHERE lower(n.properties_json) CONTAINS {_q(session_id.lower())} " if session_id else ""
            result = self._conn.execute(f"MATCH (n:GraphNode) {where}RETURN n.kind, count(*)")
            counts: dict[str, int] = {}
            for row in _rows(result):
                counts[str(row[0])] = int(row[1])
            return {"backend": "kuzu", "graph_path": str(self.graph_path), "schema": "compact", "counts": counts}
        where = f"WHERE n.session_id = {_q(session_id)} " if session_id else ""
        result = self._conn.execute(f"MATCH (n:GraphNode) {where}RETURN n.status, count(*)")
        counts: dict[str, int] = {}
        for row in _rows(result):
            counts[str(row[0])] = int(row[1])
        return {"backend": "kuzu", "graph_path": str(self.graph_path), "counts": counts}

    def set_node_status(self, node_id: str, status: str) -> bool:
        self._ensure_writable("set_node_status")
        if self._uses_compact_node_schema():
            return False
        self._conn.execute(
            "MATCH (n:GraphNode) "
            f"WHERE n.id = {_q(node_id)} "
            f"SET n.status = {_q(status)}, n.updated_at = {_q(_now())}"
        )
        return True

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

    def _uses_compact_node_schema(self) -> bool:
        return "properties_json" in self._node_columns() and "status" not in self._node_columns()

    def _ensure_writable(self, operation: str) -> None:
        if self.read_only:
            raise RuntimeError(f"kuzu_read_only_store_cannot_{operation}")

    def _node_columns(self) -> set[str]:
        cached = getattr(self, "_node_columns_cache", None)
        if cached is not None:
            return cached
        try:
            result = self._conn.execute("CALL table_info('GraphNode') RETURN *")
            columns = {str(row[1]) for row in _rows(result) if len(row) > 1}
        except Exception:
            columns = set()
        self._node_columns_cache = columns
        return columns

    def _upsert_compact_node(self, node: GraphNode) -> None:
        metadata = dict(node.metadata)
        metadata.setdefault("status", node.status)
        metadata.setdefault("scope", node.scope)
        metadata.setdefault("session_id", node.session_id)
        metadata.setdefault("source_app", node.source_app)
        metadata.setdefault("evidence_id", node.evidence_id)
        metadata.setdefault("commit_id", node.commit_id)
        metadata.setdefault("created_at", node.created_at)
        metadata.setdefault("updated_at", node.updated_at)

        packet_id = str(metadata.get("packet_id") or metadata.get("source_packet_id") or "")
        if not packet_id and node.kind == "Packet":
            packet_id = node.id
        commit_sha = str(
            node.commit_id
            or metadata.get("commit_sha")
            or metadata.get("source_commit_sha")
            or metadata.get("short_sha")
            or ""
        )
        properties_json = json.dumps(metadata, sort_keys=True)

        try:
            self._conn.execute(
                "CREATE (:GraphNode {"
                f"id: {_q(node.id)}, "
                f"kind: {_q(node.kind)}, "
                f"packet_id: {_q(packet_id)}, "
                f"commit_sha: {_q(commit_sha)}, "
                f"label: {_q(node.label)}, "
                f"summary: {_q(node.summary)}, "
                f"properties_json: {_q(properties_json)}"
                "})"
            )
        except Exception as exc:
            if metadata.get("immutable_session_graph_node") and "duplicated primary key" in str(exc).lower():
                return
            self._conn.execute(
                "MATCH (n:GraphNode) "
                f"WHERE n.id = {_q(node.id)} "
                "SET "
                f"n.kind = {_q(node.kind)}, "
                f"n.packet_id = {_q(packet_id)}, "
                f"n.commit_sha = {_q(commit_sha)}, "
                f"n.label = {_q(node.label)}, "
                f"n.summary = {_q(node.summary)}, "
                f"n.properties_json = {_q(properties_json)}"
            )

    def _upsert_compact_edge(self, edge: GraphEdge, *, created_at: str) -> None:
        metadata = dict(edge.metadata)
        metadata.setdefault("edge_id", edge.id)
        metadata.setdefault("weight", edge.weight)
        metadata.setdefault("confidence", edge.confidence)
        metadata.setdefault("evidence_id", edge.evidence_id)
        metadata.setdefault("created_at", created_at)
        properties_json = json.dumps(metadata, sort_keys=True)

        self._conn.execute(
            "MATCH (a:GraphNode)-[e:GraphEdge]->(b:GraphNode) "
            f"WHERE a.id = {_q(edge.source_id)} AND b.id = {_q(edge.target_id)} AND e.kind = {_q(edge.kind)} "
            "DELETE e"
        )
        self._conn.execute(
            "MATCH (a:GraphNode), (b:GraphNode) "
            f"WHERE a.id = {_q(edge.source_id)} AND b.id = {_q(edge.target_id)} "
            f"CREATE (a)-[:GraphEdge {{kind: {_q(edge.kind)}, properties_json: {_q(properties_json)}}}]->(b)"
        )


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


def _compact_search_where(query: str, kinds: list[str] | None) -> str:
    clauses: list[str] = []
    terms = _terms(query)
    if terms:
        clauses.append(
            "("
            + " OR ".join(
                f"lower(n.label) CONTAINS {_q(term)} OR lower(n.summary) CONTAINS {_q(term)} "
                f"OR lower(n.properties_json) CONTAINS {_q(term)}"
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


def _compact_row_to_node(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[6] if len(row) > 6 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    status = str(metadata.get("status") or metadata.get("node_status") or "session_final")
    session_id = str(metadata.get("session_id") or metadata.get("source_session_id") or "")
    evidence_refs = metadata.get("evidence_refs") if isinstance(metadata.get("evidence_refs"), list) else []
    evidence_id = str(evidence_refs[0]) if evidence_refs else str(metadata.get("evidence_id") or "")
    return {
        "id": row[0],
        "kind": row[1],
        "label": row[4],
        "summary": row[5],
        "status": status,
        "scope": str(metadata.get("scope") or "session"),
        "session_id": session_id,
        "project_id": str(metadata.get("project_id") or "default"),
        "source_app": str(metadata.get("source_app") or "unknown"),
        "evidence_id": evidence_id,
        "commit_id": row[3],
        "created_at": str(metadata.get("created_at") or ""),
        "updated_at": str(metadata.get("updated_at") or ""),
        "packet_id": row[2],
        "commit_sha": row[3],
        "metadata": {
            **metadata,
            "packet_id": metadata.get("packet_id") or row[2],
            "commit_sha": metadata.get("commit_sha") or row[3],
        },
    }


def _row_to_edge(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[8] if len(row) > 8 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row[0],
        "source_id": row[1],
        "target_id": row[2],
        "kind": row[3],
        "weight": float(row[4] or 0.0),
        "confidence": float(row[5] or 0.0),
        "evidence_id": row[6],
        "created_at": row[7],
        "metadata": metadata,
    }


def _compact_row_to_edge(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[3] if len(row) > 3 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    source_id = str(row[0])
    target_id = str(row[1])
    kind = str(row[2])
    return {
        "id": str(metadata.get("id") or f"edge:{source_id}:{kind}:{target_id}"),
        "source_id": source_id,
        "target_id": target_id,
        "kind": kind,
        "weight": float(metadata.get("weight") or 1.0),
        "confidence": float(metadata.get("confidence") or 0.8),
        "evidence_id": str(metadata.get("evidence_id") or ""),
        "created_at": str(metadata.get("created_at") or ""),
        "metadata": metadata,
    }


def _q(value: object) -> str:
    return json.dumps("" if value is None else str(value))


def _kuzu_buffer_pool_size() -> int:
    raw = os.environ.get("AMO_KUZU_GRAPH_BUFFER_POOL_SIZE", "").strip()
    if raw:
        try:
            return max(256 * 1024 * 1024, int(raw))
        except ValueError:
            pass
    return 0


def _kuzu_max_threads() -> int:
    raw = os.environ.get("AMO_KUZU_MAX_THREADS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 2


def _terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in str(text or "").split():
        clean = re.sub(r"[^a-z0-9_.-]+", "", token.lower())
        if len(clean) <= 2:
            continue
        if clean in SEARCH_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", clean):
            continue
        terms.append(clean)
    return terms


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
