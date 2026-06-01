"""Daemon graph and graph-debug routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....application.services.session_detail import build_session_detail_fallback
from ....graph.diagnostics import debug_drain, debug_graph, debug_hooks, debug_qwen
from ....graph.service import GraphRagService
from ....graph.store import GraphBackendUnavailable
from ....llm.qwen import QwenUnavailable
from .. import dashboard
from ..coordination import DRAIN_LOCK
from ..coordination import GRAPH_WRITE_LOCK
from ..coordination import READ_ONLY_GET_GRAPH_PATHS
from ..coordination import bounded_int
from ..graph_access import read_graph_service

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

_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
JsonWriter = Callable[[int, dict[str, Any]], bool]


def _is_graph_get_path(path: str) -> bool:
    return (
        path == "/api/graph/sessions"
        or path == "/api/graph/session-detail"
        or path.startswith("/api/graph/")
        or path.startswith("/api/debug/")
        or path == "/api/graph-merge-status"
    )


def handle_graph_get(
    *,
    path: str,
    query: dict[str, list[str]],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle read-side graph, central graph, and debug graph routes."""
    if not _is_graph_get_path(path):
        return False

    raw_limit = (query.get("limit") or ["25"])[0]
    limit = bounded_int(raw_limit, default=25, minimum=1, maximum=500)
    session_id = (query.get("session_id") or [""])[0]
    repo_id = (query.get("repo_id") or [""])[0]

    if path == "/api/graph/sessions":
        sessions_limit = bounded_int(raw_limit, default=80, minimum=1, maximum=500)
        try:
            write_json(200, dashboard.session_overview_fast(settings, limit=sessions_limit, repo_id=repo_id))
        except Exception as exc:
            write_json(500, {"ok": False, "error": str(exc)})
        return True

    if path == "/api/graph/session-detail" and (query.get("include_graph") or ["false"])[0].lower() != "true":
        detail_limit = bounded_int(raw_limit, default=120, minimum=1, maximum=500)
        try:
            write_json(200, build_session_detail_fallback(settings, session_id=session_id, limit=detail_limit))
        except Exception as exc:
            write_json(500, {"ok": False, "error": str(exc)})
        return True

    try:
        if path == "/api/debug/hooks":
            write_json(200, debug_hooks(settings))
            return True
        if path == "/api/debug/qwen":
            sample = (query.get("sample") or ["Classify a decision lookup query."])[0]
            write_json(200, debug_qwen(settings, sample=sample))
            return True

        if path in READ_ONLY_GET_GRAPH_PATHS:
            graph = read_graph_service(
                settings,
                repo_id=repo_id if path in {"/api/graph/central", "/api/graph/version-flow"} else "",
            )
        else:
            graph = GraphRagService(settings)
        try:
            if path == "/api/graph/status" or path == "/api/graph-merge-status":
                write_json(200, graph.merge_status(session_id=session_id))
                return True
            if path == "/api/graph/session-context":
                write_json(200, graph.current_context(session_id=session_id, limit=limit))
                return True
            if path == "/api/graph/raw-evidence":
                graph_query = (query.get("query") or query.get("q") or [""])[0]
                write_json(200, graph.raw_evidence(query=graph_query, limit=limit))
                return True
            if path == "/api/graph/work-trace":
                commit = (query.get("commit") or ["HEAD"])[0] or "HEAD"
                cwd = (query.get("cwd") or [""])[0] or None
                write_json(200, graph.work_trace(commit=commit, cwd=cwd))
                return True
            if path == "/api/graph/session-detail":
                write_json(200, graph.session_detail(session_id=session_id, limit=limit))
                return True
            if path == "/api/graph/central":
                full = (query.get("full") or ["false"])[0].lower() == "true"
                central_limit = bounded_int(
                    raw_limit,
                    default=5000 if full else 360,
                    minimum=1,
                    maximum=10000 if full else 500,
                )
                write_json(200, graph.central_graph(limit=central_limit, full=full, repo_id=repo_id))
                return True
            if path == "/api/graph/version-flow":
                commit = (query.get("commit") or [""])[0]
                write_json(200, graph.version_flow(commit=commit, session_id=session_id, repo_id=repo_id, limit=limit))
                return True
            if path == "/api/debug/drain":
                with DRAIN_LOCK, GRAPH_WRITE_LOCK:
                    write_json(200, debug_drain(graph._new_drain(), session_id=session_id))  # noqa: SLF001
                return True
            if path == "/api/debug/graph":
                write_json(200, debug_graph(graph, session_id=session_id))
                return True
        finally:
            graph.close()
    except _CLIENT_ABORT_ERRORS:
        return True
    except (GraphBackendUnavailable, QwenUnavailable) as exc:
        _write_graph_unavailable(
            path=path,
            settings=settings,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            error=exc,
            write_json=write_json,
            status=200,
        )
        return True
    except Exception as exc:
        _write_graph_unavailable(
            path=path,
            settings=settings,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            error=exc,
            write_json=write_json,
            status=500,
        )
        return True

    return False


def _write_graph_unavailable(
    *,
    path: str,
    settings: Settings,
    session_id: str,
    repo_id: str,
    limit: int,
    error: Exception,
    write_json: JsonWriter,
    status: int,
) -> None:
    if path == "/api/graph/session-detail":
        write_json(
            200,
            build_session_detail_fallback(
                settings,
                session_id=session_id,
                limit=limit,
                error=error,
            ),
        )
        return
    if path in {"/api/graph/status", "/api/graph-merge-status", "/api/graph/central"}:
        write_json(
            200,
            dashboard.graph_unavailable_payload(settings, path=path, repo_id=repo_id, error=error, limit=limit),
        )
        return
    write_json(status, {"ok": False, "error": str(error)})


def handle_graph_post(
    *,
    path: str,
    payload: dict[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle graph POST routes that mutate or query production graph state."""
    if path == "/graph/search":
        graph = read_graph_service(settings)
        try:
            limit = bounded_int(str(payload.get("limit") or ""), default=8, minimum=1, maximum=50)
            result = graph.graph_search(
                query=str(payload.get("query") or ""),
                limit=limit,
                include_raw=bool(payload.get("include_raw")),
                include_historical=bool(payload.get("include_historical")),
            )
            write_json(200, result)
        finally:
            graph.close()
        return True

    if path == "/graph/drain":
        with DRAIN_LOCK, GRAPH_WRITE_LOCK:
            graph = GraphRagService(settings)
            try:
                limit = bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                max_windows = bounded_int(
                    str(payload.get("max_windows") or ""),
                    default=settings.drain_max_windows_per_run,
                    minimum=1,
                    maximum=25,
                )
                result = graph.drain_evidence(
                    limit=limit,
                    session_id=str(payload.get("session_id") or ""),
                    max_windows=max_windows,
                )
                write_json(200, result)
            finally:
                graph.close()
        return True

    if path == "/graph/work-trace":
        graph = read_graph_service(settings)
        try:
            result = graph.work_trace(
                commit=str(payload.get("commit") or "HEAD"),
                cwd=payload.get("cwd") or None,
            )
            write_json(200, result)
        finally:
            graph.close()
        return True

    if path == "/graph/version-flow":
        repo_id = str(payload.get("repo_id") or "")
        graph = read_graph_service(settings, repo_id=repo_id)
        try:
            limit = bounded_int(str(payload.get("limit") or ""), default=100, minimum=1, maximum=500)
            result = graph.version_flow(
                commit=str(payload.get("commit") or ""),
                session_id=str(payload.get("session_id") or ""),
                repo_id=repo_id,
                limit=limit,
            )
            write_json(200, result)
        finally:
            graph.close()
        return True

    return False


__all__ = ["GRAPH_ROUTES", "handle_graph_get", "handle_graph_post"]
