"""Daemon local memory API routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....memory import MemoryService
from ..coordination import bounded_int

MEMORY_ROUTES = (
    "/api/dashboard",
    "/api/graph",
    "/api/sessions",
    "/api/events",
    "/api/memories",
    "/api/retrieval-runs",
    "/api/retrieval-runs/{run_id}",
    "/memory/search",
)

_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_memory_get(
    *,
    path: str,
    query: dict[str, list[str]],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle dashboard, memory, session, and retrieval-run GET routes."""
    if not path.startswith("/api/"):
        return False

    svc = MemoryService(settings)
    try:
        svc.init_db()
        raw_limit = (query.get("limit") or ["25"])[0]
        limit = bounded_int(raw_limit, default=25, minimum=1, maximum=100)
        session_id = (query.get("session_id") or [""])[0] or None
        if path == "/api/dashboard":
            write_json(200, {"ok": True, "data": svc.dashboard_snapshot(limit=limit)})
            return True
        if path == "/api/graph":
            include_historical = (query.get("include_historical") or ["false"])[0].lower() == "true"
            graph_query = (query.get("query") or query.get("q") or [""])[0] or None
            min_confidence_raw = (query.get("min_confidence") or [""])[0]
            min_confidence = float(min_confidence_raw) if min_confidence_raw else None
            graph_limit = bounded_int(raw_limit, default=100, minimum=10, maximum=500)
            write_json(
                200,
                {
                    "ok": True,
                    "graph": svc.graph_snapshot(
                        query=graph_query,
                        session_id=session_id,
                        limit=graph_limit,
                        include_historical=include_historical,
                        relation=(query.get("relation") or [""])[0] or None,
                        node_type=(query.get("node_type") or [""])[0] or None,
                        memory_type=(query.get("memory_type") or [""])[0] or None,
                        min_confidence=min_confidence,
                    ),
                },
            )
            return True
        if path == "/api/sessions":
            write_json(200, {"ok": True, "sessions": svc.list_sessions(limit=limit)})
            return True
        if path == "/api/events":
            write_json(200, {"ok": True, "events": svc.list_events(session_id=session_id, limit=limit)})
            return True
        if path == "/api/memories":
            include_historical = (query.get("include_historical") or ["true"])[0].lower() != "false"
            write_json(
                200,
                {
                    "ok": True,
                    "memories": svc.list_memory_units(
                        session_id=session_id,
                        limit=limit,
                        include_historical=include_historical,
                    ),
                },
            )
            return True
        if path == "/api/retrieval-runs":
            write_json(200, {"ok": True, "retrieval_runs": svc.list_retrieval_runs(limit=limit)})
            return True
        if path.startswith("/api/retrieval-runs/"):
            run_id = path.rsplit("/", 1)[-1]
            write_json(200, {"ok": True, "detail": svc.retrieval_run_detail(run_id)})
            return True
    except _CLIENT_ABORT_ERRORS:
        return True
    except Exception as exc:
        write_json(500, {"ok": False, "error": str(exc)})
        return True
    finally:
        svc.close()
    return False


def handle_memory_post(
    *,
    path: str,
    payload: dict[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle local memory POST routes."""
    if path != "/memory/search":
        return False

    svc = MemoryService(settings)
    try:
        svc.init_db()
        limit = bounded_int(str(payload.get("limit") or ""), default=10, minimum=1, maximum=50)
        result = svc.search_memories(
            query=str(payload.get("query") or ""),
            session_id=payload.get("session_id") or None,
            limit=limit,
        )
        write_json(200, {"ok": True, "results": result})
    finally:
        svc.close()
    return True


__all__ = ["MEMORY_ROUTES", "handle_memory_get", "handle_memory_post"]
