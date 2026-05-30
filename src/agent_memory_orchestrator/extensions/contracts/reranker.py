from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(slots=True, frozen=True)
class RerankRequest:
    query: str
    candidates: Sequence[Mapping[str, Any]]
    limit: int = 10
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RerankResult:
    ranked_ids: Sequence[str]
    scores: Mapping[str, float] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class Reranker(Protocol):
    name: str
    version: str

    def rerank(self, request: RerankRequest) -> RerankResult:
        """Return a deterministic order over candidate ids."""
