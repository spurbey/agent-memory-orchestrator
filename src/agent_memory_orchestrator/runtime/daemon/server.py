from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...core.config import Settings
from ...graph.service import GraphRagService
from ...graph.store import GraphBackendUnavailable
from ...integrations.connectors.slack import SlackConnectorService
from ...memory import MemoryService
from ...llm.qwen import QwenUnavailable
from . import auto_drain as _auto_drain
from . import dashboard as _dashboard
from .coordination import GRAPH_WRITE_LOCK as _GRAPH_WRITE_LOCK
from .coordination import bounded_int as _bounded_int
from .logging import daemon_log as _daemon_log
from .owner_lock import DaemonAlreadyRunning
from .owner_lock import DaemonOwnerLock
from .routes.jobs import handle_job_retry_post as _handle_job_retry_post
from .routes.jobs import handle_jobs_get as _handle_jobs_get
from .routes.health import handle_health_get as _handle_health_get
from .routes.graph import handle_graph_get as _handle_graph_get
from .routes.graph import handle_graph_post as _handle_graph_post
from .routes.retrieval import handle_graph_retrieval_post as _handle_graph_retrieval_post
from .routes.web import graph_workbench_html as _graph_workbench_html
from .routes.web import load_web_asset
from .routes.web import session_cockpit_html as _session_cockpit_html
from .routes.web import web_asset_bytes as _web_asset_bytes

_DaemonOwnerLock = DaemonOwnerLock
_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
_load_web_asset = load_web_asset


def _start_auto_drain_worker(settings: Settings) -> Any:
    return _auto_drain.start_auto_drain_worker(settings)


def _auto_drain_loop(settings: Settings) -> None:
    _auto_drain._auto_drain_loop(settings)


def _run_auto_drain_once(settings: Settings) -> dict[str, Any]:
    return _auto_drain.run_auto_drain_once(settings)


def _list_repositories_fast(settings: Settings, *, limit: int = 200) -> dict[str, Any]:
    return _dashboard.list_repositories_fast(settings, limit=limit)


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
        if _handle_health_get(path=path, settings=self.settings, write_json=self._write_json):
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
        if _handle_graph_get(path=path, query=query, settings=self.settings, write_json=self._write_json):
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
            if _handle_graph_post(
                path=self.path,
                payload=payload,
                settings=self.settings,
                write_json=self._write_json,
            ):
                return
            if _handle_graph_retrieval_post(
                path=self.path,
                payload=payload,
                settings=self.settings,
                write_json=self._write_json,
            ):
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


