from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ....domain.semantic_harness import HarnessQueryRequest
from ....domain.semantic_harness import HarnessQueryResponse
from ....domain.semantic_harness import HarnessProjectionSet
from ....domain.semantic_harness import HarnessProjectionDocument
from ....domain.semantic_harness import SourceFile
from ....domain.semantic_harness import StructuralHarnessGraph
from ....domain.semantic_harness import answer_structural_query
from ....domain.semantic_harness import build_structural_graph
from ....domain.semantic_harness import repo_id_for_root
from .projection_cache import InMemoryProjectionCache
from .projection_cache import ProjectionCache
from .repository import RepoBootstrapOptions
from .repository import read_repo_source_files


@dataclass(slots=True, frozen=True)
class StructuralRepoBootstrapResult:
    graph: StructuralHarnessGraph
    repo_root: Path
    file_count: int
    skipped_count: int
    skipped: tuple[dict[str, str], ...]


class StructuralHarnessService:
    """Structural-only harness service for Phase 1.

    Graph persistence, AMO imports, vectors, and semantic enrichment are later
    phases. Projection cache is intentionally in-memory and rebuildable.
    """

    def __init__(self, *, projection_cache: ProjectionCache | None = None) -> None:
        self._projection_cache = projection_cache or InMemoryProjectionCache()

    def bootstrap(self, *, repo_id: str, files: tuple[SourceFile, ...]) -> StructuralHarnessGraph:
        return build_structural_graph(repo_id, files)

    def bootstrap_repo(
        self,
        repo_root: str | Path,
        *,
        repo_id: str = "",
        options: RepoBootstrapOptions | None = None,
    ) -> StructuralRepoBootstrapResult:
        snapshot = read_repo_source_files(repo_root, options)
        resolved_repo_id = repo_id or repo_id_for_root(snapshot.repo_root)
        graph = build_structural_graph(resolved_repo_id, snapshot.files)
        return StructuralRepoBootstrapResult(
            graph=graph,
            repo_root=snapshot.repo_root,
            file_count=len(snapshot.files),
            skipped_count=len(snapshot.skipped),
            skipped=snapshot.skipped,
        )

    def query(self, graph: StructuralHarnessGraph, request: HarnessQueryRequest) -> HarnessQueryResponse:
        return answer_structural_query(
            graph,
            request,
            projection_document_provider=lambda: self.projection_documents(graph),
        )

    def projection_set(self, graph: StructuralHarnessGraph) -> HarnessProjectionSet:
        return self._projection_cache.get_or_build(graph)

    def projection_documents(self, graph: StructuralHarnessGraph) -> tuple[HarnessProjectionDocument, ...]:
        return self.projection_set(graph).documents


__all__ = ["StructuralHarnessService", "StructuralRepoBootstrapResult"]
