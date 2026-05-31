"""CLI command groups for graph operations."""

from __future__ import annotations

GRAPH_COMMANDS = (
    "graph-search",
    "graph-status",
    "graph-drain",
    "graph-cleanup-noisy",
    "graph-consolidate",
    "graph-cache-status",
    "graph-rebuild-cache",
    "graph-finalize-session",
    "graph-rebuild-central",
    "graph-version-flow",
    "graph-build-session",
    "graph-session-search",
)

__all__ = ["GRAPH_COMMANDS"]
