"""Daemon graph route names."""

from __future__ import annotations

GRAPH_ROUTES = (
    "/api/graph",
    "/api/graph/sessions",
    "/api/graph/session-detail",
    "/api/graph/status",
    "/api/graph/session-context",
    "/api/graph/raw-evidence",
    "/api/graph/work-trace",
    "/api/graph/central",
    "/api/graph/version-flow",
    "/graph/search",
    "/graph/drain",
    "/graph/retrieve",
)

__all__ = ["GRAPH_ROUTES"]
