from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ....domain.semantic_harness import DEFAULT_PROJECTION_VERSION
from ....domain.semantic_harness import HarnessProjectionSet
from ....domain.semantic_harness import StructuralHarnessGraph
from ....domain.semantic_harness import build_projection_set
from ....domain.semantic_harness import graph_snapshot_identity
from ....domain.semantic_harness import projection_set_id


class ProjectionCache(Protocol):
    def get_or_build(
        self,
        graph: StructuralHarnessGraph,
        *,
        projection_version: str = DEFAULT_PROJECTION_VERSION,
    ) -> HarnessProjectionSet: ...


@dataclass(slots=True, frozen=True)
class ProjectionCacheStats:
    size: int
    hits: int
    misses: int


class InMemoryProjectionCache:
    """Application-layer projection cache keyed by graph snapshot and projection version."""

    def __init__(self) -> None:
        self._sets: dict[str, HarnessProjectionSet] = {}
        self._hits = 0
        self._misses = 0

    def get_or_build(
        self,
        graph: StructuralHarnessGraph,
        *,
        projection_version: str = DEFAULT_PROJECTION_VERSION,
    ) -> HarnessProjectionSet:
        snapshot = graph_snapshot_identity(graph)
        cache_key = projection_set_id(snapshot.graph_snapshot_id, projection_version=projection_version)
        if cached := self._sets.get(cache_key):
            self._hits += 1
            return cached
        projection = build_projection_set(graph, projection_version=projection_version)
        self._sets[projection.projection_id] = projection
        self._misses += 1
        return projection

    def stats(self) -> ProjectionCacheStats:
        return ProjectionCacheStats(size=len(self._sets), hits=self._hits, misses=self._misses)


__all__ = ["InMemoryProjectionCache", "ProjectionCache", "ProjectionCacheStats"]
