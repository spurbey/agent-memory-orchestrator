from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from agent_memory_orchestrator.peer.protocol import PeerMessage


class PeerNetdError(RuntimeError):
    """Raised when the local libp2p sidecar rejects or fails a request."""


@dataclass(slots=True, frozen=True)
class PeerNetdClient:
    """Small localhost client for the Go libp2p sidecar.

    AMO keeps room policy, memory retrieval, and context assembly in Python.
    The sidecar only owns network reachability and message transport.
    """

    base_url: str = "http://127.0.0.1:8788"
    timeout_seconds: float = 10.0

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def connect(self, addr: str) -> dict[str, Any]:
        if not addr:
            raise ValueError("addr is required")
        return self._request("POST", "/connect", {"addr": addr})

    def bootstrap(self, addrs: list[str] | None = None) -> dict[str, Any]:
        return self._request("POST", "/bootstrap", {"addrs": addrs or []})

    def peers(self) -> dict[str, Any]:
        return self._request("GET", "/peers")

    def rendezvous_register(self, addr: str, namespace: str, ttl_seconds: int = 7200) -> dict[str, Any]:
        if not addr:
            raise ValueError("addr is required")
        if not namespace:
            raise ValueError("namespace is required")
        return self._request(
            "POST",
            "/rendezvous/register",
            {"addr": addr, "namespace": namespace, "ttl_seconds": ttl_seconds},
        )

    def rendezvous_discover(
        self,
        addr: str,
        namespace: str,
        limit: int = 20,
        connect: bool = True,
    ) -> list[dict[str, Any]]:
        if not addr:
            raise ValueError("addr is required")
        if not namespace:
            raise ValueError("namespace is required")
        payload = self._request(
            "POST",
            "/rendezvous/discover",
            {"addr": addr, "namespace": namespace, "limit": limit, "connect": connect},
        )
        peers = payload.get("peers", [])
        return peers if isinstance(peers, list) else []

    def send_message(self, to_peer_id: str, message: PeerMessage) -> dict[str, Any]:
        if not to_peer_id:
            raise ValueError("to_peer_id is required")
        return self.send_raw(
            to_peer_id=to_peer_id,
            message={
                "type": message.message_type,
                "room_id": message.room_id,
                "from_node_id": message.from_node_id,
                "to_node_id": message.to_node_ids[0] if message.to_node_ids else "",
                "payload": {
                    "content": message.content,
                    "confidence": message.confidence,
                },
                "citations": list(message.citations),
                "metadata": dict(message.metadata),
                "created_at": message.created_at,
            },
        )

    def send_raw(self, to_peer_id: str, message: dict[str, Any]) -> dict[str, Any]:
        if not to_peer_id:
            raise ValueError("to_peer_id is required")
        if not isinstance(message, dict):
            raise TypeError("message must be a dict")
        return self._request("POST", "/send", {"to_peer_id": to_peer_id, "message": _strip_empty(message)})

    def messages(self) -> list[dict[str, Any]]:
        payload = self._request("GET", "/messages")
        messages = payload.get("messages", [])
        return messages if isinstance(messages, list) else []

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url.rstrip("/") + path
        body = None
        headers = {}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                response_payload = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            response_text = exc.read().decode("utf-8", errors="replace")
            raise PeerNetdError(f"{method} {path} failed with HTTP {exc.code}: {response_text}") from exc
        except error.URLError as exc:
            raise PeerNetdError(f"{method} {path} failed: {exc.reason}") from exc
        except TimeoutError as exc:
            raise PeerNetdError(f"{method} {path} timed out") from exc

        if not isinstance(response_payload, dict):
            raise PeerNetdError(f"{method} {path} returned non-object JSON")
        if response_payload.get("ok") is False:
            raise PeerNetdError(str(response_payload.get("error") or f"{method} {path} failed"))
        return response_payload


def _strip_empty(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_empty(item) for key, item in value.items() if item not in ("", None, [], {})}
    if isinstance(value, list):
        return [_strip_empty(item) for item in value]
    return value
