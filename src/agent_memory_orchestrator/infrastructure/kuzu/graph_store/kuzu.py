from __future__ import annotations

import gc
import json
from pathlib import Path
from typing import Any

from .helpers import _compact_row_to_edge
from .helpers import _compact_row_to_node
from .helpers import _compact_search_where
from .helpers import _kuzu_buffer_pool_size
from .helpers import _kuzu_max_threads
from .helpers import _node_props
from .helpers import _now
from .helpers import _q
from .helpers import _row_to_edge
from .helpers import _row_to_node
from .helpers import _rows
from .helpers import _search_where
from .models import GraphBackendUnavailable
from .models import GraphEdge
from .models import GraphNode


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
