from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...core.config import Settings
from ...graph.diagnostics import debug_drain, debug_graph, debug_hooks, debug_qwen
from ...graph.service import GraphRagService
from ...graph.service import build_session_detail_fallback
from ...graph.store import GraphBackendUnavailable
from ...integrations.connectors.slack import SlackConnectorService
from ...memory import MemoryService
from ...llm.qwen import QwenUnavailable
from ...reasoning_graph.jobs import ProductionSessionJobStore
from . import auto_drain as _auto_drain
from . import dashboard as _dashboard
from . import graph_access as _graph_access
from .coordination import DRAIN_LOCK as _DRAIN_LOCK
from .coordination import GRAPH_WRITE_LOCK as _GRAPH_WRITE_LOCK
from .coordination import READ_ONLY_GET_GRAPH_PATHS as _READ_ONLY_GET_GRAPH_PATHS
from .coordination import bounded_int as _bounded_int
from .logging import daemon_log as _daemon_log
from .owner_lock import DaemonAlreadyRunning
from .owner_lock import DaemonOwnerLock
from .payloads import optional_payload_path as _payload_optional_path
from .payloads import settings_with_payload_paths as _payload_settings_with_paths
from .routes.jobs import handle_job_retry_post as _handle_job_retry_post
from .routes.jobs import handle_jobs_get as _handle_jobs_get
from .routes.web import graph_workbench_html as _graph_workbench_html
from .routes.web import load_web_asset
from .routes.web import session_cockpit_html as _session_cockpit_html
from .routes.web import web_asset_bytes as _web_asset_bytes

_DaemonOwnerLock = DaemonOwnerLock
_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
_load_web_asset = load_web_asset


def _read_graph_service(settings: Settings, *, repo_id: str = "") -> GraphRagService:
    return _graph_access.read_graph_service(settings, repo_id=repo_id)


def _start_auto_drain_worker(settings: Settings) -> Any:
    return _auto_drain.start_auto_drain_worker(settings)


def _auto_drain_loop(settings: Settings) -> None:
    _auto_drain._auto_drain_loop(settings)


def _run_auto_drain_once(settings: Settings) -> dict[str, Any]:
    return _auto_drain.run_auto_drain_once(settings)


def _list_repositories_fast(settings: Settings, *, limit: int = 200) -> dict[str, Any]:
    return _dashboard.list_repositories_fast(settings, limit=limit)


def _session_overview_fast(settings: Settings, *, limit: int = 80, repo_id: str = "") -> dict[str, Any]:
    return _dashboard.session_overview_fast(settings, limit=limit, repo_id=repo_id)


def _stage_diagnostics(job_store: ProductionSessionJobStore, job_id: str, stage: str) -> dict[str, Any]:
    return _dashboard.stage_diagnostics(job_store, job_id, stage)


def _dashboard_graph_unavailable_payload(settings: Settings, *, path: str, repo_id: str, error: Exception, limit: int) -> dict[str, Any]:
    return _dashboard.graph_unavailable_payload(settings, path=path, repo_id=repo_id, error=error, limit=limit)


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
            job_store = ProductionSessionJobStore(self.settings)
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
                    "production_marker": reset_marker,
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
        if _handle_jobs_get(path=path, query=query, settings=self.settings, write_json=self._write_json):
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
                if path in _READ_ONLY_GET_GRAPH_PATHS:
                    graph = _read_graph_service(
                        self.settings,
                        repo_id=repo_id if path in {"/api/graph/central", "/api/graph/version-flow"} else "",
                    )
                else:
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
            if _handle_job_retry_post(
                path=self.path,
                payload=payload,
                settings=self.settings,
                write_json=self._write_json,
            ):
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
                graph = _read_graph_service(self.settings)
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
                graph = _read_graph_service(self.settings)
                try:
                    result = graph.work_trace(
                        commit=str(payload.get("commit") or "HEAD"),
                        cwd=payload.get("cwd") or None,
                    )
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/retrieval-build":
                graph_settings = _settings_with_payload_paths(self.settings, payload, prefer_retrieval=True)
                graph = _read_graph_service(graph_settings)
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
                graph = _read_graph_service(graph_settings)
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
                graph = _read_graph_service(graph_settings, repo_id=str(payload.get("repo_id") or ""))
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
                            "hint": "Build the production retrieval index and embeddings for the configured graph, or configure retrieval_graph_path/retrieval_db_path.",
                            "graph_path": str(graph_settings.graph_path),
                            "db_path": str(graph_settings.retrieval_db_path),
                        }
                    self._write_json(200, result)
                finally:
                    graph.close()
                return
            if self.path == "/graph/version-flow":
                repo_id = str(payload.get("repo_id") or "")
                graph = _read_graph_service(self.settings, repo_id=repo_id)
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


def _optional_payload_path(payload: dict[str, Any], key: str) -> Any:
    return _payload_optional_path(payload, key)


def _settings_with_payload_paths(settings: Settings, payload: dict[str, Any], *, prefer_retrieval: bool = False) -> Settings:
    return _payload_settings_with_paths(settings, payload, prefer_retrieval=prefer_retrieval)


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

    try:
        owner_lock = DaemonOwnerLock.acquire(settings)
    except DaemonAlreadyRunning as exc:
        _daemon_log(settings, "daemon_start_rejected", reason="daemon_already_running", error=str(exc))
        print(f"amo-daemon already running for AMO home: {settings.home}")
        return 2

    with owner_lock:
        AmoHandler.settings = settings
        server = ThreadingHTTPServer((host, port), AmoHandler)
        _start_auto_drain_worker(settings)
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


