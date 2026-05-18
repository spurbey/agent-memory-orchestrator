from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from ..core.config import Settings
from .service import PeerService

_CLIENT_ABORT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)


class PeerHandler(BaseHTTPRequestHandler):
    settings: Settings

    def _write_json(self, status: int, payload: dict[str, Any]) -> bool:
        body = json.dumps(payload, indent=2).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except _CLIENT_ABORT_ERRORS:
            return False
        return True

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        query = parse_qs(parsed_url.query)
        svc = PeerService(self.settings)
        try:
            if path == "/peer/health":
                self._write_json(200, svc.status())
                return
            if path == "/peer/capabilities":
                self._write_json(200, svc.capabilities())
                return
            if path == "/peer/rooms":
                self._write_json(200, svc.list_rooms())
                return
            if path.startswith("/peer/rooms/") and path.endswith("/context"):
                room_id = path.split("/")[-2]
                viewer = (query.get("viewer") or [""])[0]
                self._write_json(200, svc.context_pack(room_id, viewer_node_id=viewer or None))
                return
            if path.startswith("/peer/rooms/"):
                room_id = path.rsplit("/", 1)[-1]
                self._write_json(200, svc.room_detail(room_id))
                return
            self._write_json(404, {"ok": False, "error": "not found"})
        except _CLIENT_ABORT_ERRORS:
            return
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        svc = PeerService(self.settings)
        try:
            payload = self._read_json()
            if path == "/peer/rooms/invite":
                result = svc.receive_invite(payload)
                self._write_json(200 if result.get("ok") else 403, result)
                return
            if path == "/peer/messages":
                result = svc.receive_message(payload)
                self._write_json(200 if result.get("ok") else 400, result)
                return
            if path.startswith("/peer/rooms/") and path.endswith("/summary"):
                room_id = path.split("/")[-2]
                result = svc.update_summary(room_id, summary_md=str(payload.get("summary_md") or ""))
                self._write_json(200 if result.get("ok") else 400, result)
                return
            self._write_json(404, {"ok": False, "error": "not found"})
        except json.JSONDecodeError as exc:
            self._write_json(400, {"ok": False, "error": f"invalid json: {exc}"})
        except _CLIENT_ABORT_ERRORS:
            return
        except Exception as exc:
            self._write_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, format: str, *args: object) -> None:
        return


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the AMO peer listener for Tailscale/direct transports")
    parser.add_argument("--amo-home", help="AMO home directory containing peer config and room state.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args(argv)
    if args.amo_home:
        os.environ["AMO_HOME"] = args.amo_home
    settings = Settings.load()

    PeerHandler.settings = settings
    server = ThreadingHTTPServer((args.host, args.port), PeerHandler)
    print(f"amo-peer listening on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
