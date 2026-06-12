from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from .interfaces import EdgeKey


@dataclass(slots=True)
class InMemoryHarnessGraphStore:
    _repo_id: str
    _nodes: dict[str, HarnessNode] = field(default_factory=dict)
    _edges: dict[EdgeKey, HarnessEdge] = field(default_factory=dict)

    @classmethod
    def from_graph(cls, graph: StructuralHarnessGraph) -> InMemoryHarnessGraphStore:
        store = cls(graph.repo_id)
        for node in graph.nodes:
            store.upsert_node(node)
        for edge in graph.edges:
            store.upsert_edge(edge)
        return store

    @property
    def repo_id(self) -> str:
        return self._repo_id

    def get_node(self, node_id: str) -> HarnessNode | None:
        return self._nodes.get(node_id)

    def get_edge(self, source_id: str, target_id: str, kind: str) -> HarnessEdge | None:
        return self._edges.get(_edge_key(source_id, target_id, kind))

    def node_exists(self, node_id: str) -> bool:
        return node_id in self._nodes

    def edge_exists(self, source_id: str, target_id: str, kind: str) -> bool:
        return _edge_key(source_id, target_id, kind) in self._edges

    def upsert_node(self, node: HarnessNode) -> bool:
        if node.id in self._nodes:
            return False
        self._nodes[node.id] = node
        return True

    def replace_node(self, node: HarnessNode) -> None:
        self._nodes[node.id] = node

    def upsert_edge(self, edge: HarnessEdge) -> bool:
        key = _edge_key(edge.source_id, edge.target_id, edge.kind)
        if key in self._edges:
            return False
        self._edges[key] = edge
        return True

    def replace_edge(self, edge: HarnessEdge) -> None:
        self._edges[_edge_key(edge.source_id, edge.target_id, edge.kind)] = edge

    def outgoing(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]:
        return tuple(
            edge
            for edge in self._edges.values()
            if edge.source_id == node_id and (not kind or edge.kind == kind)
        )

    def edge_keys(self) -> tuple[EdgeKey, ...]:
        return tuple(sorted(self._edges))

    def node_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._nodes))

    def to_graph(self) -> StructuralHarnessGraph:
        return StructuralHarnessGraph(
            repo_id=self.repo_id,
            nodes=tuple(self._nodes.values()),
            edges=tuple(self._edges.values()),
        )


def _edge_key(source_id: str, target_id: str, kind: str) -> EdgeKey:
    return (source_id, target_id, kind)


__all__ = ["InMemoryHarnessGraphStore"]
