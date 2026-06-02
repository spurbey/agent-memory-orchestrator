from __future__ import annotations

import json
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from .models import PeerNode
from .netd_client import PeerNetdClient, PeerNetdError


def normalize_netd_envelope(envelope: dict[str, Any]) -> dict[str, Any]:
    message = envelope.get("message")
    if not isinstance(message, dict):
        return {"ok": False, "error": "netd envelope missing message object"}

    message_type = str(message.get("type") or "").strip()
    payload = message.get("payload")
    if not isinstance(payload, dict):
        payload = {
            "room_id": message.get("room_id"),
            "type": message_type,
            "from_node_id": message.get("from_node_id"),
            "to_node_ids": [message.get("to_node_id")] if message.get("to_node_id") else [],
            "content": "",
            "citations": message.get("citations") or [],
            "metadata": message.get("metadata") or {},
            "created_at": message.get("created_at") or "",
        }

    auth = {
        "authenticated": bool(envelope.get("signature")),
        "auth": "netd:hmac-sha256" if envelope.get("signature") else "netd:none",
        "from_node_id": envelope.get("from_node_id") or message.get("from_node_id") or "",
        "remote_peer_id": str(envelope.get("remote_peer_id") or "").strip(),
        "payload_sha256": envelope.get("payload_sha256"),
    }

    payload.setdefault("type", message_type)
    payload.setdefault("room_id", message.get("room_id"))
    payload.setdefault("from_node_id", message.get("from_node_id"))
    payload.setdefault("to_node_ids", [message.get("to_node_id")] if message.get("to_node_id") else [])
    payload.setdefault("citations", message.get("citations") or [])
    payload.setdefault("created_at", message.get("created_at") or "")
    return {"ok": True, "message_type": message_type, "payload": payload, "auth": auth}


def build_netd_raw_message(
    *,
    local_node_id: str,
    peer: PeerNode,
    payload: dict[str, Any],
    message_type: str,
    room_id: str,
) -> dict[str, Any]:
    return {
        "type": message_type,
        "room_id": room_id,
        "from_node_id": local_node_id,
        "to_node_id": peer.node_id,
        "payload": payload,
        "citations": payload.get("citations") if isinstance(payload.get("citations"), list) else [],
        "metadata": {
            "to_node_id": peer.node_id,
            "to_peer_id": peer.peer_id,
            "transport": "libp2p",
        },
    }


def connect_peer_via_netd(peer: PeerNode, client: PeerNetdClient) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    if peer.rendezvous_addr and peer.rendezvous_namespace:
        try:
            discovered = client.rendezvous_discover(peer.rendezvous_addr, peer.rendezvous_namespace, connect=True)
            results.append({"ok": True, "type": "rendezvous", "peers": discovered})
        except PeerNetdError as exc:
            results.append({"ok": False, "type": "rendezvous", "error": str(exc)})
    for addr in (*peer.multiaddrs, *peer.relay_addrs):
        try:
            results.append({"ok": True, "type": "connect", "addr": addr, "response": client.connect(addr)})
        except PeerNetdError as exc:
            results.append({"ok": False, "type": "connect", "addr": addr, "error": str(exc)})
    return results


def post_json(url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(url, data=data, method="POST", headers={"Content-Type": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - peer URL is explicitly configured.
            body = response.read().decode("utf-8")
            parsed = json.loads(body) if body else {}
            return {"ok": bool(parsed.get("ok", True)), "status": response.status, "response": parsed}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": exc.code, "error": body or str(exc)}
    except (URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
