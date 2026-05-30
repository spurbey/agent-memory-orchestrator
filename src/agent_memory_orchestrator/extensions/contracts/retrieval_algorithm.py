from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(slots=True, frozen=True)
class RetrievalRequest:
    query: str
    repo_id: str = ""
    session_id: str = ""
    limit: int = 10
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RetrievalResultItem:
    id: str
    score: float
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class RetrievalAlgorithm(Protocol):
    name: str
    version: str

    def search(self, request: RetrievalRequest) -> Sequence[RetrievalResultItem]:
        """Return ranked retrieval candidates for an AMO query."""
