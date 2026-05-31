"""Daemon retrieval route names."""

from __future__ import annotations

RETRIEVAL_ROUTES = (
    "/api/retrieval-runs",
    "/api/retrieval-runs/{run_id}",
    "/graph/retrieval-build",
    "/graph/retrieval-embed",
    "/graph/retrieve",
)

__all__ = ["RETRIEVAL_ROUTES"]
