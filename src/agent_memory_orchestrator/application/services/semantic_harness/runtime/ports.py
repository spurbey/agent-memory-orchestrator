from __future__ import annotations

from typing import Protocol

from agent_memory_orchestrator.domain.semantic_harness import HarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph


class HarnessGraphRepository(Protocol):
    """Application boundary for loading and replacing persisted harness graphs."""

    def load(self, repo_id: str) -> HarnessGraphStore | None: ...

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> HarnessGraphStore: ...


__all__ = ["HarnessGraphRepository"]
