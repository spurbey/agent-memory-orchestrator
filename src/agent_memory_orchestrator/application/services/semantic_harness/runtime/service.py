from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.application.services.semantic_harness.projection_cache import InMemoryProjectionCache
from agent_memory_orchestrator.application.services.semantic_harness.projection_cache import ProjectionCache
from agent_memory_orchestrator.application.services.semantic_harness.repository import RepoBootstrapOptions
from agent_memory_orchestrator.application.services.semantic_harness.runtime.memory import InMemoryHarnessGraphRepository
from agent_memory_orchestrator.application.services.semantic_harness.runtime.models import HarnessRuntimeBootstrapResult
from agent_memory_orchestrator.application.services.semantic_harness.runtime.models import HarnessRuntimeDeltaApplyResult
from agent_memory_orchestrator.application.services.semantic_harness.runtime.ports import HarnessGraphRepository
from agent_memory_orchestrator.application.services.semantic_harness.structural import StructuralHarnessService
from agent_memory_orchestrator.domain.semantic_harness import GraphUpdateDelta
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryResponse
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import apply_graph_update_delta
from agent_memory_orchestrator.domain.semantic_harness import graph_snapshot_identity


class SemanticHarnessRuntimeService:
    """Coordinates persisted graph lifecycle, projection refresh, and queries."""

    def __init__(
        self,
        *,
        graph_repository: HarnessGraphRepository | None = None,
        projection_cache: ProjectionCache | None = None,
        structural: StructuralHarnessService | None = None,
    ) -> None:
        self._graph_repository = graph_repository or InMemoryHarnessGraphRepository()
        self._projection_cache = projection_cache or InMemoryProjectionCache()
        self._structural = structural or StructuralHarnessService(projection_cache=self._projection_cache)

    def bootstrap_repo(
        self,
        repo_root: str | Path,
        *,
        repo_id: str = "",
        options: RepoBootstrapOptions | None = None,
    ) -> HarnessRuntimeBootstrapResult:
        bootstrap = self._structural.bootstrap_repo(repo_root, repo_id=repo_id, options=options)
        self._graph_repository.replace_from_graph(bootstrap.graph)
        projection = self._projection_cache.get_or_build(bootstrap.graph)
        snapshot = graph_snapshot_identity(bootstrap.graph)
        return HarnessRuntimeBootstrapResult(
            repo_root=bootstrap.repo_root,
            repo_id=bootstrap.graph.repo_id,
            file_count=bootstrap.file_count,
            skipped_count=bootstrap.skipped_count,
            skipped=bootstrap.skipped,
            graph_snapshot=snapshot,
            projection_id=projection.projection_id,
            projection_document_count=projection.document_count,
        )

    def load_graph(self, repo_id: str) -> StructuralHarnessGraph | None:
        store = self._graph_repository.load(repo_id)
        return store.to_graph() if store is not None else None

    def query(self, repo_id: str, request: HarnessQueryRequest) -> HarnessQueryResponse:
        graph = self.load_graph(repo_id)
        if graph is None:
            return HarnessQueryResponse(
                status="unavailable",
                intent_requested=request.intent,
                intent_used=request.intent,
                intent_correction=None,
                cards=(),
                next_actions=(),
                trace={"nodes": [], "edges": [], "versions": [], "occurrences": []},
                warnings=(f"repo_not_bootstrapped:{repo_id}",),
            )
        return self._structural.query(graph, request)

    def apply_delta(self, delta: GraphUpdateDelta) -> HarnessRuntimeDeltaApplyResult:
        store = self._graph_repository.load(delta.repo_id)
        if store is None:
            raise ValueError(f"repo_not_bootstrapped:{delta.repo_id}")
        apply_result = apply_graph_update_delta(store, delta)
        graph = store.to_graph()
        projection = self._projection_cache.get_or_build(graph)
        return HarnessRuntimeDeltaApplyResult(
            repo_id=delta.repo_id,
            graph_snapshot=graph_snapshot_identity(graph),
            projection=projection,
            apply_result=apply_result,
        )


__all__ = ["SemanticHarnessRuntimeService"]
