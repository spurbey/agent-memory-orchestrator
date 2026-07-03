from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from helixdb import IndexSpec
from helixdb import NodeRef
from helixdb import Predicate
from helixdb import PropertyInput
from helixdb import define_params
from helixdb import g
from helixdb import param
from helixdb import read_batch
from helixdb import write_batch

from agent_memory_orchestrator.domain.semantic_harness.models import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness.models import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness.models import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.store.interfaces import EdgeKey

from .client import HelixHarnessClient
from .codec import EDGE_PROPERTIES
from .codec import NODE_PROPERTIES
from .codec import edge_from_properties
from .codec import edge_properties
from .codec import json_dumps
from .codec import node_from_properties
from .codec import node_properties
from .codec import result_properties

_MANIFEST_LABEL = "HarnessRepo"


class HelixHarnessGraphStore:
    """HelixDB-backed Semantic Harness graph store."""

    def __init__(self, client: HelixHarnessClient, repo_id: str) -> None:
        self._client = client
        self._repo_id = repo_id
        self._node_kinds: tuple[str, ...] = ()
        self._edge_kinds: tuple[str, ...] = ()
        self._node_kind_by_id: dict[str, str] = {}
        self._load_manifest()

    @property
    def repo_id(self) -> str:
        return self._repo_id

    @property
    def exists(self) -> bool:
        return bool(self._node_kinds)

    def replace_graph(self, graph: StructuralHarnessGraph) -> None:
        if graph.repo_id != self.repo_id:
            raise ValueError(f"repo_id_mismatch:{self.repo_id}!={graph.repo_id}")
        node_kinds = tuple(sorted({node.kind for node in graph.nodes}))
        edge_kinds = tuple(sorted({edge.kind for edge in graph.edges}))
        self._delete_repo_graph(node_kinds=tuple(sorted({*self._node_kinds, *node_kinds})))
        self._ensure_indexes(node_kinds, edge_kinds)
        self._write_nodes(graph.nodes)
        self._node_kind_by_id = {node.id: node.kind for node in graph.nodes}
        self._write_edges(graph.edges)
        self._write_manifest(node_kinds=node_kinds, edge_kinds=edge_kinds)
        self._node_kinds = node_kinds
        self._edge_kinds = edge_kinds

    def get_node(self, node_id: str) -> HarnessNode | None:
        kind = self._node_kind_by_id.get(node_id)
        kinds = (kind,) if kind else self._node_kinds
        for node_kind in kinds:
            rows = self._read_nodes(node_kind, node_id=node_id)
            if rows:
                node = node_from_properties(rows[0])
                self._node_kind_by_id[node.id] = node.kind
                return node
        return None

    def get_edge(self, source_id: str, target_id: str, kind: str) -> HarnessEdge | None:
        rows = self._read_edges(kind, source_id=source_id, target_id=target_id)
        return edge_from_properties(rows[0], kind=kind) if rows else None

    def node_exists(self, node_id: str) -> bool:
        return self.get_node(node_id) is not None

    def edge_exists(self, source_id: str, target_id: str, kind: str) -> bool:
        return self.get_edge(source_id, target_id, kind) is not None

    def upsert_node(self, node: HarnessNode) -> bool:
        if self.node_exists(node.id):
            return False
        self._ensure_indexes((node.kind,), ())
        self._write_nodes((node,))
        self._node_kind_by_id[node.id] = node.kind
        self._node_kinds = tuple(sorted({*self._node_kinds, node.kind}))
        self._write_manifest(node_kinds=self._node_kinds, edge_kinds=self._edge_kinds)
        return True

    def replace_node(self, node: HarnessNode) -> None:
        existing = self.get_node(node.id)
        if existing is None:
            self.upsert_node(node)
            return
        query = (
            write_batch()
            .var_as(
                "node",
                g()
                .n_with_label(existing.kind)
                .where(Predicate.eq("node_id", node.id))
                .set_property("label", node.label)
                .set_property("status", node.status)
                .set_property("summary", node.summary)
                .set_property("path", str(node.metadata.get("path") or ""))
                .set_property("qualified_name", str(node.metadata.get("qualified_name") or ""))
                .set_property("line_start", int(node.metadata.get("line_start") or 0))
                .set_property("line_end", int(node.metadata.get("line_end") or 0))
                .set_property("metadata_json", json_dumps(node.metadata)),
            )
            .returning(["node"])
        )
        self._client.send(query.to_dynamic_request())

    def upsert_edge(self, edge: HarnessEdge) -> bool:
        if self.edge_exists(edge.source_id, edge.target_id, edge.kind):
            return False
        self._ensure_indexes((), (edge.kind,))
        self._write_edges((edge,))
        self._edge_kinds = tuple(sorted({*self._edge_kinds, edge.kind}))
        self._write_manifest(node_kinds=self._node_kinds, edge_kinds=self._edge_kinds)
        return True

    def replace_edge(self, edge: HarnessEdge) -> None:
        if self.edge_exists(edge.source_id, edge.target_id, edge.kind):
            self._drop_edge(edge.source_id, edge.target_id, edge.kind)
        self._write_edges((edge,))

    def outgoing(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]:
        kinds = (kind,) if kind else self._edge_kinds
        edges = [
            edge_from_properties(row, kind=edge_kind)
            for edge_kind in kinds
            for row in self._read_edges(edge_kind, source_id=node_id)
        ]
        return tuple(sorted(edges, key=lambda edge: (edge.kind, edge.target_id)))

    def edge_keys(self) -> tuple[EdgeKey, ...]:
        return tuple(sorted((edge.source_id, edge.target_id, edge.kind) for edge in self._all_edges()))

    def node_ids(self) -> tuple[str, ...]:
        return tuple(sorted(node.id for node in self._all_nodes()))

    def to_graph(self) -> StructuralHarnessGraph:
        nodes = tuple(self._all_nodes())
        edges = tuple(self._all_edges())
        self._node_kind_by_id = {node.id: node.kind for node in nodes}
        return StructuralHarnessGraph(repo_id=self.repo_id, nodes=nodes, edges=edges)

    def _all_nodes(self) -> list[HarnessNode]:
        return [
            node_from_properties(row)
            for kind in self._node_kinds
            for row in self._read_nodes(kind)
        ]

    def _all_edges(self) -> list[HarnessEdge]:
        return [
            edge_from_properties(row, kind=kind)
            for kind in self._edge_kinds
            for row in self._read_edges(kind)
        ]

    def _read_nodes(self, kind: str, *, node_id: str = "") -> list[dict[str, Any]]:
        traversal = g().n_with_label(kind).where(Predicate.eq("repo_id", self.repo_id))
        if node_id:
            traversal = traversal.where(Predicate.eq("node_id", node_id))
        query = read_batch().var_as("nodes", traversal.value_map(["$id", *NODE_PROPERTIES])).returning(["nodes"])
        return result_properties(self._client.send(query.to_dynamic_request()), "nodes")

    def _read_edges(
        self,
        kind: str,
        *,
        source_id: str = "",
        target_id: str = "",
    ) -> list[dict[str, Any]]:
        traversal = g().e_with_label(kind).edge_has("repo_id", self.repo_id)
        if source_id:
            traversal = traversal.edge_has("source_id", source_id)
        if target_id:
            traversal = traversal.edge_has("target_id", target_id)
        query = read_batch().var_as("edges", traversal.edge_properties()).returning(["edges"])
        return result_properties(self._client.send(query.to_dynamic_request()), "edges")

    def _write_nodes(self, nodes: Iterable[HarnessNode]) -> None:
        by_kind: dict[str, list[HarnessNode]] = {}
        for node in nodes:
            by_kind.setdefault(node.kind, []).append(node)
        params = define_params({"rows": param.array(param.object())})
        for kind, grouped in by_kind.items():
            query = write_batch().for_each_param(
                "rows",
                write_batch().var_as(
                    "created",
                    g().add_n(
                        kind,
                        {name: PropertyInput.param(name) for name in NODE_PROPERTIES},
                    ),
                ),
            ).returning(["created"])
            rows = [node_properties(node) for node in grouped]
            for batch in _batches(rows, self._client.config.batch_size):
                self._client.send(query.to_dynamic_request(params, {"rows": batch}))

    def _write_edges(self, edges: Iterable[HarnessEdge]) -> None:
        grouped: dict[tuple[str, str, str], list[HarnessEdge]] = {}
        for edge in edges:
            source_kind = self._node_kind_by_id.get(edge.source_id) or self._kind_for_node(edge.source_id)
            target_kind = self._node_kind_by_id.get(edge.target_id) or self._kind_for_node(edge.target_id)
            if not source_kind or not target_kind:
                raise ValueError(f"missing_edge_endpoint:{edge.source_id}->{edge.target_id}")
            grouped.setdefault((edge.kind, source_kind, target_kind), []).append(edge)
        params = define_params({"rows": param.array(param.object())})
        for (kind, source_kind, target_kind), grouped_edges in grouped.items():
            body = (
                write_batch()
                .var_as("source", g().n_with_label(source_kind).where(Predicate.eq_param("node_id", "source_id")))
                .var_as("target", g().n_with_label(target_kind).where(Predicate.eq_param("node_id", "target_id")))
                .var_as(
                    "created",
                    g().n(NodeRef.var("source")).add_e(
                        kind,
                        NodeRef.var("target"),
                        {name: PropertyInput.param(name) for name in EDGE_PROPERTIES},
                    ),
                )
            )
            query = write_batch().for_each_param("rows", body).returning(["created"])
            rows = [edge_properties(edge, repo_id=self.repo_id) for edge in grouped_edges]
            for batch in _batches(rows, self._client.config.batch_size):
                self._client.send(query.to_dynamic_request(params, {"rows": batch}))

    def _drop_edge(self, source_id: str, target_id: str, kind: str) -> None:
        source_kind = self._kind_for_node(source_id)
        target_kind = self._kind_for_node(target_id)
        if not source_kind or not target_kind:
            return
        query = (
            write_batch()
            .var_as("source", g().n_with_label(source_kind).where(Predicate.eq("node_id", source_id)))
            .var_as("target", g().n_with_label(target_kind).where(Predicate.eq("node_id", target_id)))
            .var_as("dropped", g().n(NodeRef.var("source")).drop_edge_labeled(NodeRef.var("target"), kind))
            .returning(["dropped"])
        )
        self._client.send(query.to_dynamic_request())

    def _kind_for_node(self, node_id: str) -> str:
        if kind := self._node_kind_by_id.get(node_id):
            return kind
        node = self.get_node(node_id)
        return node.kind if node else ""

    def _delete_repo_graph(self, *, node_kinds: tuple[str, ...] | None = None) -> None:
        for kind in node_kinds or self._node_kinds:
            query = (
                write_batch()
                .var_as("nodes", g().n_with_label(kind).where(Predicate.eq("repo_id", self.repo_id)).drop())
                .returning(["nodes"])
            )
            self._client.send(query.to_dynamic_request())
        query = (
            write_batch()
            .var_as("manifest", g().n_with_label(_MANIFEST_LABEL).where(Predicate.eq("repo_id", self.repo_id)).drop())
            .returning(["manifest"])
        )
        self._client.send(query.to_dynamic_request())
        self._node_kind_by_id.clear()

    def _load_manifest(self) -> None:
        query = (
            read_batch()
            .var_as(
                "manifest",
                g()
                .n_with_label(_MANIFEST_LABEL)
                .where(Predicate.eq("repo_id", self.repo_id))
                .value_map(["repo_id", "node_kinds_json", "edge_kinds_json"]),
            )
            .returning(["manifest"])
        )
        rows = result_properties(self._client.send(query.to_dynamic_request()), "manifest")
        if not rows:
            return
        self._node_kinds = _json_string_tuple(rows[0].get("node_kinds_json"))
        self._edge_kinds = _json_string_tuple(rows[0].get("edge_kinds_json"))

    def _write_manifest(self, *, node_kinds: tuple[str, ...], edge_kinds: tuple[str, ...]) -> None:
        existing = result_properties(
            self._client.send(
                read_batch()
                .var_as(
                    "manifest",
                    g().n_with_label(_MANIFEST_LABEL).where(Predicate.eq("repo_id", self.repo_id)).value_map(["repo_id"]),
                )
                .returning(["manifest"])
                .to_dynamic_request()
            ),
            "manifest",
        )
        if existing:
            query = (
                write_batch()
                .var_as(
                    "manifest",
                    g()
                    .n_with_label(_MANIFEST_LABEL)
                    .where(Predicate.eq("repo_id", self.repo_id))
                    .set_property("node_kinds_json", json.dumps(node_kinds))
                    .set_property("edge_kinds_json", json.dumps(edge_kinds)),
                )
                .returning(["manifest"])
            )
        else:
            query = (
                write_batch()
                .var_as(
                    "manifest",
                    g().add_n(
                        _MANIFEST_LABEL,
                        {
                            "repo_id": self.repo_id,
                            "node_kinds_json": json.dumps(node_kinds),
                            "edge_kinds_json": json.dumps(edge_kinds),
                        },
                    ),
                )
                .returning(["manifest"])
            )
        self._client.send(query.to_dynamic_request())

    def _ensure_indexes(self, node_kinds: tuple[str, ...], edge_kinds: tuple[str, ...]) -> None:
        batch = write_batch().var_as(
            "manifest_repo_idx",
            g().create_index_if_not_exists(IndexSpec.node_unique_equality(_MANIFEST_LABEL, "repo_id")),
        )
        return_names = ["manifest_repo_idx"]
        node_index = 0
        for kind in node_kinds:
            specs = [
                IndexSpec.node_unique_equality(kind, "node_id"),
                IndexSpec.node_equality(kind, "repo_id"),
                IndexSpec.node_equality(kind, "label"),
            ]
            if kind in {"File", "Symbol", "CodeRegion", "DocSection", "DocString"}:
                specs.append(IndexSpec.node_equality(kind, "path"))
            if kind == "Symbol":
                specs.append(IndexSpec.node_equality(kind, "qualified_name"))
            for spec in specs:
                name = f"node_{node_index}"
                node_index += 1
                batch = batch.var_as(name, g().create_index_if_not_exists(spec))
                return_names.append(name)
        for index, kind in enumerate(edge_kinds):
            name = f"edge_{index}"
            batch = batch.var_as(name, g().create_index_if_not_exists(IndexSpec.edge_equality(kind, "repo_id")))
            return_names.append(name)
        self._client.send(batch.returning(return_names).to_dynamic_request())


def _json_string_tuple(value: Any) -> tuple[str, ...]:
    try:
        loaded = json.loads(str(value or "[]"))
    except json.JSONDecodeError:
        return ()
    return tuple(str(item) for item in loaded) if isinstance(loaded, list) else ()


def _batches(rows: list[dict[str, Any]], batch_size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), batch_size):
        yield rows[start : start + batch_size]


__all__ = ["HelixHarnessGraphStore"]
