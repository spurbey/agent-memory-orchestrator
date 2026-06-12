from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.domain.semantic_harness.models import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness.models import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.store.interfaces import EdgeKey

from .schema import ensure_semantic_harness_schema


class SQLiteHarnessGraphStore:
    """SQLite-backed implementation of the semantic harness graph store protocol."""

    def __init__(self, db_path: str | Path, repo_id: str) -> None:
        self._db_path = Path(db_path)
        self._repo_id = repo_id
        if str(self._db_path) != ":memory:":
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        ensure_semantic_harness_schema(self._conn)

    @classmethod
    def from_graph(cls, db_path: str | Path, graph: StructuralHarnessGraph) -> SQLiteHarnessGraphStore:
        store = cls(db_path, graph.repo_id)
        with store._conn:
            for node in graph.nodes:
                store._write_node(node)
            for edge in graph.edges:
                store._write_edge(edge)
        return store

    @property
    def repo_id(self) -> str:
        return self._repo_id

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SQLiteHarnessGraphStore:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def get_node(self, node_id: str) -> HarnessNode | None:
        row = self._conn.execute(
            """
            SELECT node_id, kind, label, repo_id, status, summary, metadata_json
            FROM semantic_harness_nodes
            WHERE repo_id = ? AND node_id = ?
            """,
            (self.repo_id, node_id),
        ).fetchone()
        return _node_from_row(row) if row is not None else None

    def get_edge(self, source_id: str, target_id: str, kind: str) -> HarnessEdge | None:
        row = self._conn.execute(
            """
            SELECT source_id, target_id, kind, weight, confidence, metadata_json
            FROM semantic_harness_edges
            WHERE repo_id = ? AND source_id = ? AND target_id = ? AND kind = ?
            """,
            (self.repo_id, source_id, target_id, kind),
        ).fetchone()
        return _edge_from_row(row) if row is not None else None

    def node_exists(self, node_id: str) -> bool:
        return (
            self._conn.execute(
                "SELECT 1 FROM semantic_harness_nodes WHERE repo_id = ? AND node_id = ? LIMIT 1",
                (self.repo_id, node_id),
            ).fetchone()
            is not None
        )

    def edge_exists(self, source_id: str, target_id: str, kind: str) -> bool:
        return (
            self._conn.execute(
                """
                SELECT 1
                FROM semantic_harness_edges
                WHERE repo_id = ? AND source_id = ? AND target_id = ? AND kind = ?
                LIMIT 1
                """,
                (self.repo_id, source_id, target_id, kind),
            ).fetchone()
            is not None
        )

    def upsert_node(self, node: HarnessNode) -> bool:
        if self.node_exists(node.id):
            return False
        self.replace_node(node)
        return True

    def replace_node(self, node: HarnessNode) -> None:
        self._write_node(node)
        self._conn.commit()

    def _write_node(self, node: HarnessNode) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO semantic_harness_nodes (
                repo_id, node_id, kind, label, status, summary, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.repo_id,
                node.id,
                node.kind,
                node.label,
                node.status,
                node.summary,
                _json_dumps(node.metadata),
            ),
        )

    def upsert_edge(self, edge: HarnessEdge) -> bool:
        if self.edge_exists(edge.source_id, edge.target_id, edge.kind):
            return False
        self.replace_edge(edge)
        return True

    def replace_edge(self, edge: HarnessEdge) -> None:
        self._write_edge(edge)
        self._conn.commit()

    def _write_edge(self, edge: HarnessEdge) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO semantic_harness_edges (
                repo_id, source_id, target_id, kind, weight, confidence, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                self.repo_id,
                edge.source_id,
                edge.target_id,
                edge.kind,
                edge.weight,
                edge.confidence,
                _json_dumps(edge.metadata),
            ),
        )

    def outgoing(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]:
        if kind:
            rows = self._conn.execute(
                """
                SELECT source_id, target_id, kind, weight, confidence, metadata_json
                FROM semantic_harness_edges
                WHERE repo_id = ? AND source_id = ? AND kind = ?
                ORDER BY kind, target_id
                """,
                (self.repo_id, node_id, kind),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT source_id, target_id, kind, weight, confidence, metadata_json
                FROM semantic_harness_edges
                WHERE repo_id = ? AND source_id = ?
                ORDER BY kind, target_id
                """,
                (self.repo_id, node_id),
            ).fetchall()
        return tuple(_edge_from_row(row) for row in rows)

    def edge_keys(self) -> tuple[EdgeKey, ...]:
        rows = self._conn.execute(
            """
            SELECT source_id, target_id, kind
            FROM semantic_harness_edges
            WHERE repo_id = ?
            ORDER BY source_id, target_id, kind
            """,
            (self.repo_id,),
        ).fetchall()
        return tuple((str(row["source_id"]), str(row["target_id"]), str(row["kind"])) for row in rows)

    def node_ids(self) -> tuple[str, ...]:
        rows = self._conn.execute(
            """
            SELECT node_id
            FROM semantic_harness_nodes
            WHERE repo_id = ?
            ORDER BY node_id
            """,
            (self.repo_id,),
        ).fetchall()
        return tuple(str(row["node_id"]) for row in rows)

    def to_graph(self) -> StructuralHarnessGraph:
        nodes = tuple(_node_from_row(row) for row in self._node_rows())
        edges = tuple(_edge_from_row(row) for row in self._edge_rows())
        return StructuralHarnessGraph(repo_id=self.repo_id, nodes=nodes, edges=edges)

    def _node_rows(self) -> Iterator[sqlite3.Row]:
        yield from self._conn.execute(
            """
            SELECT node_id, kind, label, repo_id, status, summary, metadata_json
            FROM semantic_harness_nodes
            WHERE repo_id = ?
            ORDER BY node_id
            """,
            (self.repo_id,),
        )

    def _edge_rows(self) -> Iterator[sqlite3.Row]:
        yield from self._conn.execute(
            """
            SELECT source_id, target_id, kind, weight, confidence, metadata_json
            FROM semantic_harness_edges
            WHERE repo_id = ?
            ORDER BY source_id, target_id, kind
            """,
            (self.repo_id,),
        )


def _node_from_row(row: sqlite3.Row) -> HarnessNode:
    return HarnessNode(
        id=str(row["node_id"]),
        kind=str(row["kind"]),
        label=str(row["label"]),
        repo_id=str(row["repo_id"]),
        status=str(row["status"]),
        summary=str(row["summary"]),
        metadata=_json_loads(str(row["metadata_json"])),
    )


def _edge_from_row(row: sqlite3.Row) -> HarnessEdge:
    return HarnessEdge(
        source_id=str(row["source_id"]),
        target_id=str(row["target_id"]),
        kind=str(row["kind"]),
        weight=float(row["weight"]),
        confidence=float(row["confidence"]),
        metadata=_json_loads(str(row["metadata_json"])),
    )


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_loads(value: str) -> dict[str, Any]:
    loaded = json.loads(value) if value else {}
    return loaded if isinstance(loaded, dict) else {}


__all__ = ["SQLiteHarnessGraphStore"]
