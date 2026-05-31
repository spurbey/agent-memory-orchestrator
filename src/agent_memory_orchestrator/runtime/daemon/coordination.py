from __future__ import annotations

import threading
from contextlib import nullcontext
from typing import Any, ContextManager

GRAPH_WRITE_LOCK = threading.RLock()
DRAIN_LOCK = threading.RLock()
PRODUCTION_GRAPH_WRITE_STAGES = frozenset({"kuzu_write", "central_version_merge"})
READ_ONLY_GET_GRAPH_PATHS = frozenset(
    {
        "/api/graph/status",
        "/api/graph-merge-status",
        "/api/graph/session-context",
        "/api/graph/raw-evidence",
        "/api/graph/work-trace",
        "/api/graph/session-detail",
        "/api/graph/central",
        "/api/graph/version-flow",
        "/api/debug/graph",
        "/api/debug/graph-cache",
    }
)


def bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def production_stage_requires_graph_write_lock(stage: str) -> bool:
    return stage in PRODUCTION_GRAPH_WRITE_STAGES


def production_stage_lock(stage: str) -> ContextManager[Any]:
    return GRAPH_WRITE_LOCK if production_stage_requires_graph_write_lock(stage) else nullcontext()


def graph_write_lock_if(condition: bool) -> ContextManager[Any]:
    return GRAPH_WRITE_LOCK if condition else nullcontext()
