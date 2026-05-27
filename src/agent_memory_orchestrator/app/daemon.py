from __future__ import annotations

import argparse
import json
import os
import threading
import time
from contextlib import nullcontext
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ContextManager
from urllib.parse import parse_qs, unquote, urlparse

from ..core.config import Settings
from ..graph.diagnostics import debug_drain, debug_graph, debug_hooks, debug_qwen
from ..graph.service import GraphRagService
from ..graph.service import build_session_detail_fallback
from ..graph.store import GraphBackendUnavailable, KuzuGraphStore
from ..integrations.connectors.slack import SlackConnectorService
from ..memory import MemoryService
from ..llm.qwen import QwenUnavailable
from ..reasoning_graph.jobs import V2SessionJobRunner
from ..reasoning_graph.jobs import V2SessionJobStore
from ..reasoning_graph.central_merge.applier import repo_central_graph_path

_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
_GRAPH_WRITE_LOCK = threading.RLock()
_DRAIN_LOCK = threading.RLock()
_V2_GRAPH_WRITE_STAGES = frozenset({"kuzu_write", "central_version_merge"})
_WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


def _load_web_asset(name: str) -> str:
    return (_WEB_ROOT / name).read_text(encoding="utf-8")


def _web_asset_bytes(name: str) -> tuple[bytes, str]:
    path = (_WEB_ROOT / name).resolve()
    root = _WEB_ROOT.resolve()
    if root not in path.parents and path != root:
        raise ValueError("invalid web asset path")
    data = path.read_bytes()
    suffix = path.suffix.lower()
    content_type = {
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".html": "text/html; charset=utf-8",
    }.get(suffix, "application/octet-stream")
    return data, content_type


def _session_cockpit_html() -> str:
    return _load_web_asset("index.html")


def _graph_workbench_html() -> str:
    return _load_web_asset("graph.html")


def _bounded_int(raw: str | None, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _v2_stage_requires_graph_write_lock(stage: str) -> bool:
    return stage in _V2_GRAPH_WRITE_STAGES


def _v2_stage_lock(stage: str) -> ContextManager[Any]:
    return _GRAPH_WRITE_LOCK if _v2_stage_requires_graph_write_lock(stage) else nullcontext()


def _graph_write_lock_if(condition: bool) -> ContextManager[Any]:
    return _GRAPH_WRITE_LOCK if condition else nullcontext()


def _list_repositories_fast(settings: Settings, *, limit: int = 200) -> dict[str, Any]:
    """Return dashboard repository scope from SQLite control state only.

    The dashboard must remain available while graph writes or Qwen jobs are in
    flight. Repo scope is operational metadata, so it should not require opening
    Kuzu or waiting on graph write locks.
    """

    job_store = V2SessionJobStore(settings)
    try:
        return {"ok": True, "repos": job_store.list_repositories(limit=limit), "source": "sqlite_control_state"}
    finally:
        job_store.close()


def _session_overview_fast(settings: Settings, *, limit: int = 80, repo_id: str = "") -> dict[str, Any]:
    """Return dashboard session cards from SQLite control rows.

    The session list is a dashboard boot dependency. It must not scan Kuzu or
    raw transcript files; detailed session views can pay that cost after the
    operator selects one session.
    """

    safe_limit = max(1, min(500, int(limit)))
    safe_repo_id = str(repo_id or "").strip()
    job_store = V2SessionJobStore(settings)
    try:
        rows: list[dict[str, Any]] = []
        jobs = job_store.list_jobs(limit=safe_limit, repo_id=safe_repo_id)
        for job in jobs:
            diagnostics = _stage_diagnostics(job_store, str(job.get("job_id") or ""), "evidence_view")
            quality = diagnostics.get("quality") if isinstance(diagnostics.get("quality"), dict) else {}
            raw_events = int(diagnostics.get("raw_record_count") or quality.get("raw_record_count") or 0)
            source_app = str(job.get("source_app") or "codex")
            rows.append(
                {
                    "session_id": str(job.get("session_id") or ""),
                    "raw_events": raw_events,
                    "source_apps": [source_app] if source_app else [],
                    "event_counts": {},
                    "first_at": str(job.get("created_at") or ""),
                    "latest_at": str(job.get("updated_at") or ""),
                    "latest_event": "closed_session",
                    "cwd": str(job.get("repo_path") or ""),
                    "repo": str(job.get("repo_path") or ""),
                    "repo_id": str(job.get("repo_id") or ""),
                    "repo_path": str(job.get("repo_path") or ""),
                    "branch": "",
                    "graph_counts": {},
                    "latest_context": None,
                }
            )
        return {
            "ok": True,
            "repo_id": safe_repo_id,
            "source": "sqlite_control_state",
            "graph_status": {"ok": True, "source": "not_loaded_for_dashboard_session_list"},
            "sessions": rows,
        }
    finally:
        job_store.close()


def _stage_diagnostics(job_store: V2SessionJobStore, job_id: str, stage: str) -> dict[str, Any]:
    row = job_store.stage_row(job_id=job_id, stage=stage)
    if not row:
        return {}
    try:
        payload = json.loads(str(row.get("diagnostics_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dashboard_graph_unavailable_payload(settings: Settings, *, path: str, repo_id: str, error: Exception, limit: int) -> dict[str, Any]:
    payload = {
        "ok": False,
        "backend": settings.graph_backend,
        "graph_path": str(settings.graph_path),
        "error": str(error),
        "error_type": type(error).__name__,
        "warning": "graph_temporarily_unavailable",
    }
    if path == "/api/graph/central":
        return {
            **payload,
            "repo_id": str(repo_id or ""),
            "nodes": [],
            "edges": [],
            "full": False,
            "limit": max(1, int(limit)),
            "status": payload,
            "warnings": ["graph_temporarily_unavailable"],
        }
    return payload


class AmoHandler(BaseHTTPRequestHandler):
    settings: Settings

    def _write_html(self, status: int, body: str) -> bool:
        encoded = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def _write_json(self, status: int, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def _write_bytes(self, status: int, body: bytes, content_type: str) -> bool:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path.startswith("/web/"):
            try:
                body, content_type = _web_asset_bytes(path.removeprefix("/web/"))
            except (OSError, ValueError):
                self._write_json(404, {"error": "asset not found"})
                return
            self._write_bytes(200, body, content_type)
            return
        if path == "/favicon.ico":
            self._write_bytes(204, b"", "image/x-icon")
            return
        if path == "/":
            self._write_html(200, _session_cockpit_html())
            return
        if path == "/dashboard":
            self._write_html(200, _session_cockpit_html())
            return
        if path == "/sessions":
            self._write_html(200, _session_cockpit_html())
            return
        if path == "/versions":
            self._write_html(200, _session_cockpit_html())
            return
        if path == "/connectors":
            self._write_html(200, _session_cockpit_html())
            return
        if path == "/graph":
            self._write_html(200, _graph_workbench_html())
            return
        if path == "/graph3d":
            self._write_html(200, _graph_workbench_html())
            return
        if path == "/health":
            job_store = V2SessionJobStore(self.settings)
            try:
                reset_marker = job_store.marker()
            finally:
                job_store.close()
            self._write_json(
                200,
                {
                    "ok": True,
                    "service": "agent-memory-orchestrator",
                    "graph_backend": self.settings.graph_backend,
                    "graph_path": str(self.settings.graph_path),
                    "qwen_runtime": self.settings.qwen_runtime,
                    "qwen_model": self.settings.qwen_model,
                    "qwen_timeout_seconds": self.settings.qwen_timeout_seconds,
                    "qwen_planner_timeout_seconds": self.settings.qwen_planner_timeout_seconds,
                    "qwen_extract_timeout_seconds": self.settings.qwen_extract_timeout_seconds,
                    "qwen_compress_timeout_seconds": self.settings.qwen_compress_timeout_seconds,
                    "qwen_num_ctx": self.settings.qwen_num_ctx,
                    "drain_max_windows_per_run": self.settings.drain_max_windows_per_run,
                    "auto_drain_enabled": self.settings.auto_drain_enabled,
                    "auto_drain_interval_seconds": self.settings.auto_drain_interval_seconds,
                    "auto_drain_record_limit": self.settings.auto_drain_record_limit,
                    "auto_embedding_batch_size": self.settings.auto_embedding_batch_size,
                    "v2_reset_marker": reset_marker,
                },
            )
            return
        if path == "/metrics":
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                self._write_json(200, svc.inspect_metrics())
            finally:
                svc.close()
            return
        if path == "/api/repos":
            raw_limit = (query.get("limit") or ["200"])[0]
            limit = _bounded_int(raw_limit, default=200, minimum=1, maximum=1000)
            self._write_json(200, _list_repositories_fast(self.settings, limit=limit))
            return
        if path == "/api/connectors/slack/status":
            try:
                svc = SlackConnectorService(self.settings)
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "slack": svc.status(),
                        "run_command": "amo-cli slack run --reply-mode answer",
                        "behavior": "Answers only when the AMO bot is tagged in a channel or thread.",
                    },
                )
            except Exception as exc:
                self._write_json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/jobs" or path.startswith("/api/jobs/"):
            raw_limit = (query.get("limit") or ["100"])[0]
            limit = _bounded_int(raw_limit, default=100, minimum=1, maximum=500)
            job_store = V2SessionJobStore(self.settings)
            try:
                if path == "/api/jobs":
                    repo_id = (query.get("repo_id") or [""])[0]
                    self._write_json(
                        200,
                        {
                            "ok": True,
                            "repo_id": repo_id,
                            "jobs": job_store.list_jobs(limit=limit, repo_id=repo_id),
                            "reset_marker": job_store.marker(),
                        },
                    )
                    return
                job_id = unquote(path.removeprefix("/api/jobs/").strip("/"))
                job = job_store.get_job(job_id)
                if job is None:
                    self._write_json(404, {"ok": False, "error": "job not found"})
                    return
                self._write_json(
                    200,
                    {
                        "ok": True,
                        "job": job,
                        "stages": job_store.list_stages(job_id),
                        "events": job_store.list_events(job_id, limit=limit),
                        "reset_marker": job_store.marker(),
                    },
                )
            finally:
                job_store.close()
            return
        if path == "/api/graph/sessions":
            raw_limit = (query.get("limit") or ["80"])[0]
            limit = _bounded_int(raw_limit, default=80, minimum=1, maximum=500)
            repo_id = (query.get("repo_id") or [""])[0]
            try:
                self._write_json(200, _session_overview_fast(self.settings, limit=limit, repo_id=repo_id))
            except Exception as exc:
                self._write_json(500, {"ok": False, "error": str(exc)})
            return
        if path == "/api/graph/session-detail" and (query.get("include_graph") or ["false"])[0].lower() != "true":
            raw_limit = (query.get("limit") or ["120"])[0]
            limit = _bounded_int(raw_limit, default=120, minimum=1, maximum=500)
            session_id = (query.get("session_id") or [""])[0]
            try:
                self._write_json(200, build_session_detail_fallback(self.settings, session_id=session_id, limit=limit))
            except Exception as exc:
                self._write_json(500, {"ok": False, "error": str(exc)})
            return
        if path.startswith("/api/graph/") or path.startswith("/api/debug/") or path == "/api/graph-merge-status":
            try:
                raw_limit = (query.get("limit") or ["25"])[0]
                limit = _bounded_int(raw_limit, default=25, minimum=1, maximum=500)
                session_id = (query.get("session_id") or [""])[0]
                repo_id = (query.get("repo_id") or [""])[0]
                if path == "/api/debug/hooks":
                    self._write_json(200, debug_hooks(self.settings))
                    return
                if path == "/api/debug/qwen":
                    sample = (query.get("sample") or ["Classify a decision lookup query."])[0]
                    self._write_json(200, debug_qwen(self.settings, sample=sample))
                    return
                graph = GraphRagService(self.settings)
                try:
                    if path == "/api/graph/status" or path == "/api/graph-merge-status":
                        self._write_json(200, graph.merge_status(session_id=session_id))
                        return
                    if path == "/api/graph/session-context":
                        self._write_json(200, graph.current_context(session_id=session_id, limit=limit))
                        return
                    if path == "/api/graph/raw-evidence":
                        graph_query = (query.get("query") or query.get("q") or [""])[0]
                        self._write_json(200, graph.raw_evidence(query=graph_query, limit=limit))
                        return
                    if path == "/api/graph/work-trace":
                        commit = (query.get("commit") or ["HEAD"])[0] or "HEAD"
                        cwd = (query.get("cwd") or [""])[0] or None
                        self._write_json(200, graph.work_trace(commit=commit, cwd=cwd))
                        return
                    if path == "/api/graph/session-detail":
                        self._write_json(200, graph.session_detail(session_id=session_id, limit=limit))
                        return
                    if path == "/api/graph/central":
                        full = (query.get("full") or ["false"])[0].lower() == "true"
                        central_limit = _bounded_int(
                            raw_limit,
                            default=5000 if full else 360,
                            minimum=1,
                            maximum=10000 if full else 500,
                        )
                        self._write_json(200, graph.central_graph(limit=central_limit, full=full, repo_id=repo_id))
                        return
                    if path == "/api/graph/version-flow":
                        commit = (query.get("commit") or [""])[0]
                        self._write_json(
                            200,
                            graph.version_flow(commit=commit, session_id=session_id, repo_id=repo_id, limit=limit),
                        )
                        return
                    if path == "/api/debug/drain":
                        with _DRAIN_LOCK, _GRAPH_WRITE_LOCK:
                            self._write_json(200, debug_drain(graph._new_drain(), session_id=session_id))  # noqa: SLF001
                        return
                    if path == "/api/debug/graph":
                        self._write_json(200, debug_graph(graph, session_id=session_id))
                        return
                    if path == "/api/debug/cleanup-noisy":
                        apply = (query.get("apply") or ["false"])[0].lower() == "true"
                        if apply:
                            with _GRAPH_WRITE_LOCK:
                                result = graph.cleanup_noisy_drafts(limit=limit, apply=True)
                        else:
                            result = graph.cleanup_noisy_drafts(limit=limit, apply=False)
                        self._write_json(200, result)
                        return
                    if path == "/api/debug/consolidate":
                        apply = (query.get("apply") or ["false"])[0].lower() == "true"
                        if apply:
                            with _GRAPH_WRITE_LOCK:
                                result = graph.consolidate_graph(limit=limit, apply=True)
                        else:
                            result = graph.consolidate_graph(limit=limit, apply=False)
                        self._write_json(200, result)
                        return
                    if path == "/api/debug/graph-cache":
                        self._write_json(200, graph.graph_cache_status())
                        return
                finally:
                    graph.close()
            except _CLIENT_ABORT_ERRORS:
                return
            except (GraphBackendUnavailable, QwenUnavailable) as exc:
                if path == "/api/graph/session-detail":
                    self._write_json(
                        200,
                        build_session_detail_fallback(
                            self.settings,
                            session_id=session_id,
                            limit=limit,
                            error=exc,
                        ),
                    )
                    return
                if path in {"/api/graph/status", "/api/graph-merge-status", "/api/graph/central"}:
                    self._write_json(
                        200,
                        _dashboard_graph_unavailable_payload(
                            self.settings,
                            path=path,
                            repo_id=repo_id,
                            error=exc,
                            limit=limit,
                        ),
                    )
                else:
                    self._write_json(200, {"ok": False, "error": str(exc)})
                return
            except Exception as exc:
                if path == "/api/graph/session-detail":
                    self._write_json(
                        200,
                        build_session_detail_fallback(
                            self.settings,
                            session_id=session_id,
                            limit=limit,
                            error=exc,
                        ),
                    )
                    return
                if path in {"/api/graph/status", "/api/graph-merge-status", "/api/graph/central"}:
                    self._write_json(
                        200,
                        _dashboard_graph_unavailable_payload(
                            self.settings,
                            path=path,
                            repo_id=repo_id,
                            error=exc,
                            limit=limit,
                        ),
                    )
                else:
                    self._write_json(500, {"ok": False, "error": str(exc)})
                return
        if path.startswith("/api/"):
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                raw_limit = (query.get("limit") or ["25"])[0]
                limit = _bounded_int(raw_limit, default=25, minimum=1, maximum=100)
                session_id = (query.get("session_id") or [""])[0] or None
                if path == "/api/dashboard":
                    self._write_json(200, {"ok": True, "data": svc.dashboard_snapshot(limit=limit)})
                    return
                if path == "/api/graph":
                    include_historical = (query.get("include_historical") or ["false"])[0].lower() == "true"
                    graph_query = (query.get("query") or query.get("q") or [""])[0] or None
                    min_confidence_raw = (query.get("min_confidence") or [""])[0]
                    min_confidence = float(min_confidence_raw) if min_confidence_raw else None
                    graph_limit = _bounded_int(raw_limit, default=100, minimum=10, maximum=500)
                    self._write_json(
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
                    return
                if path == "/api/sessions":
                    self._write_json(200, {"ok": True, "sessions": svc.list_sessions(limit=limit)})
                    return
                if path == "/api/events":
                    self._write_json(200, {"ok": True, "events": svc.list_events(session_id=session_id, limit=limit)})
                    return
                if path == "/api/memories":
                    include_historical = (query.get("include_historical") or ["true"])[0].lower() != "false"
                    self._write_json(
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
                    return
                if path == "/api/retrieval-runs":
                    self._write_json(200, {"ok": True, "retrieval_runs": svc.list_retrieval_runs(limit=limit)})
                    return
                if path.startswith("/api/retrieval-runs/"):
                    run_id = path.rsplit("/", 1)[-1]
                    self._write_json(200, {"ok": True, "detail": svc.retrieval_run_detail(run_id)})
                    return
            except _CLIENT_ABORT_ERRORS:
                return
            except Exception as exc:
                self._write_json(500, {"ok": False, "error": str(exc)})
                return
            finally:
                svc.close()
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid json: {exc}"})
            return

        try:
            if self.path.startswith("/api/jobs/") and self.path.endswith("/retry"):
                job_id = unquote(self.path.removeprefix("/api/jobs/").removesuffix("/retry").strip("/"))
                job_store = V2SessionJobStore(self.settings)
                try:
                    job = job_store.retry_job(job_id, forced_by=str(payload.get("forced_by") or "daemon-api"))
                    self._write_json(200, {"ok": True, "job": job})
                finally:
                    job_store.close()
                return
            if self.path == "/hooks/ingest":
                with _GRAPH_WRITE_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        result = graph.capture_hook(payload, default_agent=str(payload.get("agent") or "codex"))
                        self._write_json(200, {"ok": True, **result})
                    finally:
                        graph.close()
                return
            if self.path == "/graph/search":
                graph = GraphRagService(self.settings)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=8, minimum=1, maximum=50)
                    result = graph.graph_search(
                        query=str(payload.get("query") or ""),
                        limit=limit,
                        include_raw=bool(payload.get("include_raw")),
                        include_historical=bool(payload.get("include_historical")),
                    )
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/drain":
                with _DRAIN_LOCK, _GRAPH_WRITE_LOCK:
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        max_windows = _bounded_int(
                            str(payload.get("max_windows") or ""),
                            default=self.settings.drain_max_windows_per_run,
                            minimum=1,
                            maximum=25,
                        )
                        result = graph.drain_evidence(
                            limit=limit,
                            session_id=str(payload.get("session_id") or ""),
                            max_windows=max_windows,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/work-trace":
                graph = GraphRagService(self.settings)
                try:
                    result = graph.work_trace(
                        commit=str(payload.get("commit") or "HEAD"),
                        cwd=payload.get("cwd") or None,
                    )
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/cleanup-noisy":
                with _graph_write_lock_if(bool(payload.get("apply"))):
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        result = graph.cleanup_noisy_drafts(limit=limit, apply=bool(payload.get("apply")))
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/consolidate":
                with _graph_write_lock_if(bool(payload.get("apply"))):
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        result = graph.consolidate_graph(limit=limit, apply=bool(payload.get("apply")))
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/rebuild-cache":
                graph = GraphRagService(self.settings)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=5000, minimum=1, maximum=20000)
                    result = graph.rebuild_graph_cache(limit=limit)
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/retrieval-build":
                graph_settings = _settings_with_payload_paths(self.settings, payload, prefer_retrieval=True)
                graph = GraphRagService(graph_settings)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=10000, minimum=1, maximum=100000)
                    max_doc_chars = _bounded_int(
                        str(payload.get("max_doc_chars") or ""),
                        default=5000,
                        minimum=1000,
                        maximum=50000,
                    )
                    result = graph.rebuild_retrieval_index(
                        db_path=_optional_payload_path(payload, "db_path"),
                        session_id=str(payload.get("session_id") or ""),
                        repo_id=str(payload.get("repo_id") or ""),
                        limit=limit,
                        max_doc_chars=max_doc_chars,
                    )
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/retrieval-embed":
                graph_settings = _settings_with_payload_paths(self.settings, payload, prefer_retrieval=True)
                graph = GraphRagService(graph_settings)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=100, minimum=0, maximum=100000)
                    result = graph.embed_retrieval_index(
                        db_path=_optional_payload_path(payload, "db_path"),
                        session_id=str(payload.get("session_id") or ""),
                        repo_id=str(payload.get("repo_id") or ""),
                        limit=limit,
                        model=str(payload.get("model") or ""),
                        graph_scope=str(payload.get("graph_scope") or ""),
                        rebuild_faiss=bool(payload.get("rebuild_faiss", True)),
                    )
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/retrieve":
                graph_settings = _settings_with_payload_paths(self.settings, payload, prefer_retrieval=True)
                graph = GraphRagService(graph_settings)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=8, minimum=1, maximum=50)
                    try:
                        result = graph.retrieve_indexed_graph(
                            query=str(payload.get("query") or ""),
                            db_path=_optional_payload_path(payload, "db_path"),
                            session_id=str(payload.get("session_id") or ""),
                            repo_id=str(payload.get("repo_id") or ""),
                            limit=limit,
                            use_vector=bool(payload.get("use_vector", True)),
                            model=str(payload.get("model") or ""),
                            graph_scope=str(payload.get("graph_scope") or ""),
                            require_vector=bool(payload.get("require_vector", False)),
                            include_answer=bool(payload.get("include_answer", True)),
                        )
                    except ValueError as exc:
                        result = {
                            "ok": False,
                            "error": str(exc),
                            "hint": "Build the V2 retrieval index and embeddings for the configured graph, or configure retrieval_graph_path/retrieval_db_path.",
                            "graph_path": str(graph_settings.graph_path),
                            "db_path": str(graph_settings.retrieval_db_path),
                        }
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/drain-smoke":
                graph = GraphRagService(self.settings)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                    max_windows = _bounded_int(
                        str(payload.get("max_windows") or ""),
                        default=self.settings.drain_max_windows_per_run,
                        minimum=1,
                        maximum=25,
                    )
                    self._write_json(200, graph.drain_evidence_smoke(limit=limit, max_windows=max_windows))
                finally:
                    graph.close()
                return
            if self.path == "/graph/finalize-session":
                with _graph_write_lock_if(bool(payload.get("apply"))):
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=500, minimum=1, maximum=5000)
                        result = graph.finalize_session(
                            session_id=str(payload.get("session_id") or ""),
                            commit=str(payload.get("commit") or "HEAD"),
                            apply=bool(payload.get("apply")),
                            limit=limit,
                            cwd=payload.get("cwd") or None,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/rebuild-central":
                with _graph_write_lock_if(bool(payload.get("apply"))):
                    graph = GraphRagService(self.settings)
                    try:
                        limit = _bounded_int(str(payload.get("limit") or ""), default=100000, minimum=1, maximum=500000)
                        max_windows = payload.get("max_windows")
                        bounded_windows = (
                            _bounded_int(str(max_windows), default=self.settings.drain_max_windows_per_run, minimum=1, maximum=1000)
                            if max_windows
                            else None
                        )
                        result = graph.rebuild_central_from_evidence(
                            apply=bool(payload.get("apply")),
                            backup_current=bool(payload.get("backup_current")) or bool(payload.get("apply")),
                            limit=limit,
                            max_windows=bounded_windows,
                        )
                        self._write_json(200, result)
                    finally:
                        graph.close()
                return
            if self.path == "/graph/version-flow":
                repo_id = str(payload.get("repo_id") or "")
                graph_settings = self.settings
                graph_store = None
                if repo_id.strip():
                    central_graph_path = repo_central_graph_path(self.settings, repo_id)
                    graph_settings = replace(self.settings, graph_path=central_graph_path)
                    graph_store = KuzuGraphStore(central_graph_path)
                graph = GraphRagService(graph_settings, store=graph_store)
                try:
                    limit = _bounded_int(str(payload.get("limit") or ""), default=100, minimum=1, maximum=500)
                    result = graph.version_flow(
                        commit=str(payload.get("commit") or ""),
                        session_id=str(payload.get("session_id") or ""),
                        repo_id=repo_id,
                        limit=limit,
                    )
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            svc = MemoryService(self.settings)
            try:
                svc.init_db()
                if self.path == "/memory/search":
                    limit = _bounded_int(str(payload.get("limit") or ""), default=10, minimum=1, maximum=50)
                    result = svc.search_memories(
                        query=str(payload.get("query") or ""),
                        session_id=payload.get("session_id") or None,
                        limit=limit,
                    )
                    self._write_json(200, {"ok": True, "results": result})
                    return
            finally:
                svc.close()
            self._write_json(404, {"error": "not found"})
        except _CLIENT_ABORT_ERRORS:
            return
        except (GraphBackendUnavailable, QwenUnavailable) as exc:
            self._write_json(200, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


def _optional_payload_path(payload: dict[str, Any], key: str) -> Path | None:
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def _settings_with_payload_paths(settings: Settings, payload: dict[str, Any], *, prefer_retrieval: bool = False) -> Settings:
    updates: dict[str, Path] = {}
    db_path = _optional_payload_path(payload, "db_path")
    graph_path = _optional_payload_path(payload, "graph_path")
    if graph_path is None and prefer_retrieval and settings.retrieval_graph_path is not None:
        graph_path = settings.retrieval_graph_path
    if db_path is not None:
        updates["db_path"] = db_path
    if graph_path is not None:
        updates["graph_path"] = graph_path
    if not updates:
        return settings
    for path in updates.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    return replace(settings, **updates)


def _start_auto_drain_worker(settings: Settings) -> threading.Thread | None:
    if not settings.auto_drain_enabled:
        _daemon_log(settings, "auto_drain_disabled")
        return None
    worker = threading.Thread(target=_auto_drain_loop, args=(settings,), name="amo-auto-drain", daemon=True)
    worker.start()
    _daemon_log(
        settings,
        "auto_drain_started",
        interval_seconds=settings.auto_drain_interval_seconds,
        embedding_batch_size=settings.auto_embedding_batch_size,
    )
    return worker


def _auto_drain_loop(settings: Settings) -> None:
    while True:
        time.sleep(settings.auto_drain_interval_seconds)
        try:
            result = _run_auto_drain_once(settings)
            if result.get("windows_processed") or result.get("records_ingested") or result.get("v2_job_run", {}).get("ran"):
                _daemon_log(settings, "auto_drain_cycle", **result)
        except Exception as exc:
            _daemon_log(settings, "auto_drain_failed", error_type=type(exc).__name__, error=str(exc))


def _run_auto_drain_once(settings: Settings) -> dict[str, Any]:
    with _DRAIN_LOCK, _GRAPH_WRITE_LOCK:
        graph = GraphRagService(settings)
        try:
            drain = graph.drain_evidence(
                limit=settings.auto_drain_record_limit,
                max_windows=settings.drain_max_windows_per_run,
            )
        finally:
            graph.close()

    runner = V2SessionJobRunner(settings, stage_lock_factory=_v2_stage_lock)
    try:
        job_run = runner.run_next()
    finally:
        runner.close()
    result: dict[str, Any] = {
        "records_ingested": int(drain.get("records_ingested") or 0),
        "windows_processed": int(drain.get("windows_processed") or 0),
        "stopped_reason": drain.get("stopped_reason"),
        "pending_sessions": drain.get("pending_sessions"),
        "v2_job_run": job_run,
    }
    return result


def _daemon_log(settings: Settings, event: str, **fields: object) -> None:
    record = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": event,
        **fields,
    }
    try:
        path = settings.home / "logs" / "daemon.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


SESSION_COCKPIT_HTML = _session_cockpit_html()
DASHBOARD_HTML = SESSION_COCKPIT_HTML
GRAPH_WORKBENCH_HTML = _graph_workbench_html()
GRAPH_HTML = GRAPH_WORKBENCH_HTML
GRAPH3D_HTML = GRAPH_WORKBENCH_HTML


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Agent Memory Orchestrator daemon")
    parser.add_argument("--amo-home", help="AMO home directory containing config.json and .data/")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    if args.amo_home:
        os.environ["AMO_HOME"] = args.amo_home
    settings = Settings.load()
    host = args.host or settings.mcp_host
    port = args.port or settings.mcp_port
    if settings.local_only and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("AMO_LOCAL_ONLY=true requires daemon host to be localhost")

    AmoHandler.settings = settings
    _start_auto_drain_worker(settings)
    server = ThreadingHTTPServer((host, port), AmoHandler)
    print(f"amo-daemon listening on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

