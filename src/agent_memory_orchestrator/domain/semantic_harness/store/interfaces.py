from __future__ import annotations

from typing import Protocol

from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import StructuralHarnessGraph

EdgeKey = tuple[str, str, str]


class HarnessGraphStore(Protocol):
    """Minimal graph storage interface for deterministic harness graph updates."""

    @property
    def repo_id(self) -> str: ...

    def get_node(self, node_id: str) -> HarnessNode | None: ...

    def get_edge(self, source_id: str, target_id: str, kind: str) -> HarnessEdge | None: ...

    def node_exists(self, node_id: str) -> bool: ...

    def edge_exists(self, source_id: str, target_id: str, kind: str) -> bool: ...

    def upsert_node(self, node: HarnessNode) -> bool: ...

    def upsert_edge(self, edge: HarnessEdge) -> bool: ...

    def replace_edge(self, edge: HarnessEdge) -> None: ...

    def outgoing(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]: ...

    def edge_keys(self) -> tuple[EdgeKey, ...]: ...

    def node_ids(self) -> tuple[str, ...]: ...

    def to_graph(self) -> StructuralHarnessGraph: ...


__all__ = ["EdgeKey", "HarnessGraphStore"]
