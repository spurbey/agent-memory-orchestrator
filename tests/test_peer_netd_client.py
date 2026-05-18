from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent_memory_orchestrator.peer.netd_client import PeerNetdClient, PeerNetdError
from agent_memory_orchestrator.peer.protocol import PeerMessage


def test_peer_netd_client_calls_sidecar_api() -> None:
    server, state = start_fake_netd()
    try:
        client = PeerNetdClient(base_url=f"http://127.0.0.1:{server.server_port}", timeout_seconds=2)

        health = client.health()
        connected = client.connect("/ip4/127.0.0.1/tcp/9000/p2p/peer-a")
        bootstrapped = client.bootstrap(["/ip4/127.0.0.1/tcp/9001/p2p/peer-b"])
        registered = client.rendezvous_register("/ip4/127.0.0.1/tcp/9002/p2p/rv", "amo-test")
        discovered = client.rendezvous_discover("/ip4/127.0.0.1/tcp/9002/p2p/rv", "amo-test")
        sent = client.send_message(
            "peer-a",
            PeerMessage(
                room_id="room-1",
                message_type="peer_response",
                from_node_id="node-b",
                to_node_ids=("node-a",),
                content="found matching local memory",
                confidence=0.82,
                citations=("E0001",),
            ),
        )
        messages = client.messages()

        assert health["peer_id"] == "fake-peer"
        assert connected["ok"] is True
        assert bootstrapped["ok"] is True
        assert registered["ok"] is True
        assert discovered[0]["peer_id"] == "peer-a"
        assert sent["ok"] is True
        assert messages[0]["message"]["payload"]["content"] == "found matching local memory"
        assert state["connect"][0]["addr"].endswith("/peer-a")
        assert state["send"][0]["to_peer_id"] == "peer-a"
        assert state["send"][0]["message"]["citations"] == ["E0001"]
    finally:
        server.shutdown()
        server.server_close()


def test_peer_netd_client_raises_on_sidecar_error() -> None:
    server, _state = start_fake_netd(send_status=502)
    try:
        client = PeerNetdClient(base_url=f"http://127.0.0.1:{server.server_port}", timeout_seconds=2)
        with pytest.raises(PeerNetdError):
            client.send_raw("peer-a", {"type": "peer_response", "from_node_id": "node-b"})
    finally:
        server.shutdown()
        server.server_close()


def start_fake_netd(send_status: int = 200) -> tuple[ThreadingHTTPServer, dict[str, list[dict]]]:
    state: dict[str, list[dict]] = {"bootstrap": [], "connect": [], "register": [], "discover": [], "send": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/health":
                self.respond(200, {"ok": True, "peer_id": "fake-peer"})
                return
            if self.path == "/messages":
                self.respond(200, {"ok": True, "messages": [{"message": state["send"][0]["message"]}]})
                return
            if self.path == "/peers":
                self.respond(200, {"ok": True, "connected_peers": ["peer-a"], "discovered_peers": []})
                return
            self.respond(404, {"ok": False, "error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8"))
            if self.path == "/bootstrap":
                state["bootstrap"].append(payload)
                self.respond(200, {"ok": True, "results": [{"ok": True, "addr": payload["addrs"][0]}]})
                return
            if self.path == "/connect":
                state["connect"].append(payload)
                self.respond(200, {"ok": True})
                return
            if self.path == "/rendezvous/register":
                state["register"].append(payload)
                self.respond(200, {"ok": True})
                return
            if self.path == "/rendezvous/discover":
                state["discover"].append(payload)
                self.respond(200, {"ok": True, "peers": [{"peer_id": "peer-a", "addrs": ["addr-a"]}]})
                return
            if self.path == "/send":
                state["send"].append(payload)
                self.respond(send_status, {"ok": send_status == 200, "error": "send failed"})
                return
            self.respond(404, {"ok": False, "error": "not found"})

        def respond(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state
