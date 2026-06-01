"""Daemon hook ingestion routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....application.services.graph_rag import GraphRagService
from ..coordination import GRAPH_WRITE_LOCK

HOOK_ROUTES = ("/hooks/ingest",)

JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_hooks_post(
    *,
    path: str,
    payload: dict[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle capture-only agent hook ingestion."""
    if path != "/hooks/ingest":
        return False
    with GRAPH_WRITE_LOCK:
        graph = GraphRagService(settings)
        try:
            result = graph.capture_hook(payload, default_agent=str(payload.get("agent") or "codex"))
            write_json(200, {"ok": True, **result})
        finally:
            graph.close()
    return True


__all__ = ["HOOK_ROUTES", "handle_hooks_post"]
