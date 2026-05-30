from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable


@dataclass(slots=True, frozen=True)
class GraphAlgorithmContext:
    repo_id: str = ""
    graph_view_id: str = ""
    mode: str = "active"
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class GraphAlgorithmResult:
    nodes: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    edges: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class GraphAlgorithm(Protocol):
    name: str
    version: str

    def run(self, context: GraphAlgorithmContext) -> GraphAlgorithmResult:
        """Run graph analysis without mutating graph truth directly."""
