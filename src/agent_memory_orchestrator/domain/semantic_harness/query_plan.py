from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SeedKind = Literal["file", "symbol"]
TraversalDirection = Literal["incoming", "outgoing"]


@dataclass(slots=True, frozen=True)
class GraphSeed:
    """Human-facing graph seed resolved by the storage adapter."""

    kind: SeedKind
    value: str
    path_hint: str = ""


@dataclass(slots=True, frozen=True)
class EdgeExpansion:
    """Bounded structural expansion requested by an application planner."""

    kind: str
    direction: TraversalDirection
    depth: int = 1
    max_neighbors: int = 64


@dataclass(slots=True, frozen=True)
class GraphSlicePlan:
    """Backend-neutral request for the evidence graph needed by one query."""

    repo_id: str
    purpose: str
    seeds: tuple[GraphSeed, ...]
    expansions: tuple[EdgeExpansion, ...] = ()
    max_nodes: int = 256
    max_edges: int = 512


__all__ = ["EdgeExpansion", "GraphSeed", "GraphSlicePlan", "SeedKind", "TraversalDirection"]
