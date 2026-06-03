from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class GraphStorePort(Protocol):
    """Graph truth-store boundary for application services."""

    def upsert_nodes(self, nodes: Iterable[Mapping[str, Any]]) -> int:
        """Insert or update graph nodes and return the number processed."""

    def upsert_edges(self, edges: Iterable[Mapping[str, Any]]) -> int:
        """Insert or update graph edges and return the number processed."""

    def query(self, statement: str, parameters: Mapping[str, Any] | None = None) -> list[Mapping[str, Any]]:
        """Run a read query through the graph adapter."""
