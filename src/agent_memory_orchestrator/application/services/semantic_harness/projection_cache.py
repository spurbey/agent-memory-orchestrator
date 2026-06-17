from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ....domain.semantic_harness import DEFAULT_PROJECTION_VERSION
from ....domain.semantic_harness import HarnessProjectionSet
from ....domain.semantic_harness import StructuralHarnessGraph
from ....domain.semantic_harness import build_projection_set


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
    """Application-layer projection cache keyed by rendered projection identity."""

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
        projection = build_projection_set(graph, projection_version=projection_version)
        if cached := self._sets.get(projection.projection_id):
            self._hits += 1
            return cached
        self._sets[projection.projection_id] = projection
        self._misses += 1
        return projection

    def stats(self) -> ProjectionCacheStats:
        return ProjectionCacheStats(size=len(self._sets), hits=self._hits, misses=self._misses)


__all__ = ["InMemoryProjectionCache", "ProjectionCache", "ProjectionCacheStats"]
