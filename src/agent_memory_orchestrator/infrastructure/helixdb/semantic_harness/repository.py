from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from agent_memory_orchestrator.domain.semantic_harness import HarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import GraphSlicePlan
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph

from .client import HelixHarnessClient
from .config import HelixHarnessConfig
from .evidence_query import HelixEvidenceQuery
from .graph_store import HelixHarnessGraphStore


@dataclass(slots=True)
class HelixHarnessGraphRepository:
    config: HelixHarnessConfig = field(default_factory=HelixHarnessConfig.from_env)
    _stores: dict[str, HelixHarnessGraphStore] = field(default_factory=dict)
    _client: HelixHarnessClient = field(init=False)

    def __post_init__(self) -> None:
        self._client = HelixHarnessClient(self.config)

    def load(self, repo_id: str) -> HarnessGraphStore | None:
        if store := self._stores.get(repo_id):
            return store
        store = HelixHarnessGraphStore(self._client, repo_id)
        if not store.exists:
            return None
        self._stores[repo_id] = store
        return store

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> HarnessGraphStore:
        store = HelixHarnessGraphStore(self._client, graph.repo_id)
        store.replace_graph(graph)
        self._stores[graph.repo_id] = store
        return store

    def query_evidence(self, plan: GraphSlicePlan) -> StructuralHarnessGraph | None:
        if self.load(plan.repo_id) is None:
            return None
        return HelixEvidenceQuery(self._client).execute(plan)

    def healthy(self) -> bool:
        return self._client.healthy()

    def close(self) -> None:
        self._stores.clear()

    def __enter__(self) -> "HelixHarnessGraphRepository":
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


__all__ = ["HelixHarnessGraphRepository"]
