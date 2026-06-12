from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from agent_memory_orchestrator.domain.semantic_harness import HarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import InMemoryHarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph


@dataclass(slots=True)
class InMemoryHarnessGraphRepository:
    """Process-local graph repository for runtime service tests and smoke use."""

    _stores: dict[str, InMemoryHarnessGraphStore] = field(default_factory=dict)

    def load(self, repo_id: str) -> HarnessGraphStore | None:
        return self._stores.get(repo_id)

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> HarnessGraphStore:
        store = InMemoryHarnessGraphStore.from_graph(graph)
        self._stores[graph.repo_id] = store
        return store


__all__ = ["InMemoryHarnessGraphRepository"]
