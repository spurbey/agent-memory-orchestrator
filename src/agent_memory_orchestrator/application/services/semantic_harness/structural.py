from __future__ import annotations

from ....domain.semantic_harness import HarnessQueryRequest
from ....domain.semantic_harness import HarnessQueryResponse
from ....domain.semantic_harness import SourceFile
from ....domain.semantic_harness import StructuralHarnessGraph
from ....domain.semantic_harness import answer_structural_query
from ....domain.semantic_harness import build_structural_graph


class StructuralHarnessService:
    """Structural-only harness service for Phase 1.

    This service is intentionally stateless. Storage-backed graph persistence,
    AMO imports, vectors, and semantic enrichment are later phases.
    """

    def bootstrap(self, *, repo_id: str, files: tuple[SourceFile, ...]) -> StructuralHarnessGraph:
        return build_structural_graph(repo_id, files)

    def query(self, graph: StructuralHarnessGraph, request: HarnessQueryRequest) -> HarnessQueryResponse:
        return answer_structural_query(graph, request)


__all__ = ["StructuralHarnessService"]
