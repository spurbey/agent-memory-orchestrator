from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from pathlib import Path

from agent_memory_orchestrator.domain.semantic_harness import HarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph

from .graph_store import SQLiteHarnessGraphStore


@dataclass(slots=True)
class SQLiteHarnessGraphRepository:
    """SQLite-backed graph repository for the semantic harness runtime."""

    db_path: str | Path
    _stores: dict[str, SQLiteHarnessGraphStore] = field(default_factory=dict)

    def load(self, repo_id: str) -> HarnessGraphStore | None:
        if store := self._stores.get(repo_id):
            return store
        store = SQLiteHarnessGraphStore(self.db_path, repo_id)
        if not store.node_exists(repo_id):
            store.close()
            return None
        self._stores[repo_id] = store
        return store

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> HarnessGraphStore:
        if existing := self._stores.pop(graph.repo_id, None):
            existing.close()
        store = SQLiteHarnessGraphStore.from_graph(self.db_path, graph)
        self._stores[graph.repo_id] = store
        return store

    def close(self) -> None:
        for store in self._stores.values():
            store.close()
        self._stores.clear()

    def __enter__(self) -> SQLiteHarnessGraphRepository:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


__all__ = ["SQLiteHarnessGraphRepository"]
