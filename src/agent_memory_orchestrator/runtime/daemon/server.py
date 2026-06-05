from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ...core.config import Settings
from ...infrastructure.kuzu import GraphBackendUnavailable
from ...infrastructure.llm import QwenUnavailable
from . import auto_drain as _auto_drain
from . import dashboard as _dashboard
from .coordination import bounded_int as _bounded_int
from .logging import daemon_log as _daemon_log
from .owner_lock import DaemonAlreadyRunning
from .owner_lock import DaemonOwnerLock
from .routes.jobs import handle_job_retry_post as _handle_job_retry_post
from .routes.jobs import handle_jobs_get as _handle_jobs_get
from .routes.health import handle_health_get as _handle_health_get
from .routes.antelligent import handle_antelligent_get as _handle_antelligent_get
from .routes.antelligent import handle_antelligent_post as _handle_antelligent_post
from .routes.antelligent import handle_antelligent_websocket as _handle_antelligent_websocket
from .routes.graph import handle_graph_get as _handle_graph_get
from .routes.graph import handle_graph_post as _handle_graph_post
from .routes.connectors import handle_connectors_get as _handle_connectors_get
from .routes.hooks import handle_hooks_post as _handle_hooks_post
from .routes.memory import handle_memory_get as _handle_memory_get
from .routes.memory import handle_memory_post as _handle_memory_post
from .routes.retrieval import handle_graph_retrieval_post as _handle_graph_retrieval_post
from .routes.web import graph_workbench_html as _graph_workbench_html
from .routes.web import load_web_asset
from .routes.web import session_cockpit_html as _session_cockpit_html
from .routes.web import web_asset_bytes as _web_asset_bytes

_DaemonOwnerLock = DaemonOwnerLock
_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)
_load_web_asset = load_web_asset
_LOCAL_UI_ORIGIN_PREFIXES = (
    "http://localhost",
    "http://127.0.0.1",
    "https://localhost",
    "https://127.0.0.1",
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
)


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

    def _send_cors_headers(self) -> None:
        origin = str(self.headers.get("Origin") or "")
        if not origin.startswith(_LOCAL_UI_ORIGIN_PREFIXES):
            return
        self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Vary", "Origin")

    def _write_html(self, status: int, body: str) -> bool:
        encoded = body.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(encoded)))
            self._send_cors_headers()
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
            self._send_cors_headers()
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
            self._send_cors_headers()
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def do_OPTIONS(self) -> None:  # noqa: N802
        try:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self._send_cors_headers()
            self.end_headers()
        except _CLIENT_ABORT_ERRORS:
            return

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
        if _handle_antelligent_websocket(
            path=path,
            query=query,
            headers=self.headers,
            settings=self.settings,
            handler=self,
        ):
            return
        if _handle_antelligent_get(
            path=path,
            query=query,
            headers=self.headers,
            settings=self.settings,
            write_json=self._write_json,
        ):
            return
        if path == "/api/repos":
            raw_limit = (query.get("limit") or ["200"])[0]
            limit = _bounded_int(raw_limit, default=200, minimum=1, maximum=1000)
            self._write_json(200, _list_repositories_fast(self.settings, limit=limit))
            return
        if _handle_connectors_get(path=path, settings=self.settings, write_json=self._write_json):
            return
        if _handle_jobs_get(path=path, query=query, settings=self.settings, write_json=self._write_json):
            return
        if _handle_graph_get(path=path, query=query, settings=self.settings, write_json=self._write_json):
            return
        if _handle_memory_get(path=path, query=query, settings=self.settings, write_json=self._write_json):
            return
        self._write_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            self._write_json(400, {"error": f"invalid json: {exc}"})
            return

        try:
            if _handle_antelligent_post(
                path=path,
                payload=payload,
                headers=self.headers,
                settings=self.settings,
                write_json=self._write_json,
            ):
                return
            if _handle_job_retry_post(
                path=self.path,
                payload=payload,
                settings=self.settings,
                write_json=self._write_json,
            ):
                return
            if _handle_hooks_post(
                path=self.path,
                payload=payload,
                settings=self.settings,
                write_json=self._write_json,
            ):
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
            if _handle_memory_post(
                path=self.path,
                payload=payload,
                settings=self.settings,
                write_json=self._write_json,
            ):
                return
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
        owner_lock = DaemonOwnerLock.acquire(settings, host=host, port=port)
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
