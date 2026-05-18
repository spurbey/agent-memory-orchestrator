from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from ..core.config import Settings
from .auth import PeerAuthError, secret_for_peer, unwrap_payload, wrap_payload
from .models import PeerNode
from .netd_client import PeerNetdClient, PeerNetdError
from .protocol import PeerMessage
from .store import PeerStore


class PeerService:
    def __init__(
        self,
        settings: Settings,
        store: PeerStore | None = None,
        netd_client: PeerNetdClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or PeerStore(settings)
        self.netd_client = netd_client

    def init_node(self, *, node_id: str, display_name: str = "", capabilities: list[str] | None = None) -> dict[str, Any]:
        config = self.store.init_config(
            node_id=node_id,
            display_name=display_name,
            capabilities=tuple(capabilities or ("graph_retrieval", "memory_search")),
        )
        return {"ok": True, "config_path": str(self.store.config_path), "peer": config.to_dict()}

    def add_peer(
        self,
        *,
        node_id: str,
        base_url: str = "",
        peer_id: str = "",
        multiaddrs: list[str] | None = None,
        relay_addrs: list[str] | None = None,
        rendezvous_addr: str = "",
        rendezvous_namespace: str = "",
        display_name: str = "",
        capabilities: list[str] | None = None,
        trust: str = "trusted",
        shared_secret_env: str = "",
    ) -> dict[str, Any]:
        config = self.store.add_peer(
            PeerNode(
                node_id=node_id,
                base_url=base_url,
                peer_id=peer_id,
                multiaddrs=tuple(multiaddrs or ()),
                relay_addrs=tuple(relay_addrs or ()),
                rendezvous_addr=rendezvous_addr,
                rendezvous_namespace=rendezvous_namespace,
                display_name=display_name,
                capabilities=tuple(capabilities or ()),
                trust=trust,
                shared_secret_env=shared_secret_env,
            )
        )
        return {"ok": True, "peer": config.peer_by_id(node_id).to_dict() if config.peer_by_id(node_id) else None}

    def status(self) -> dict[str, Any]:
        config = self.store.load_config()
        rooms = self.store.list_rooms()
        return {
            "ok": True,
            "node": {
                "node_id": config.node_id,
                "display_name": config.display_name,
                "transport": config.transport,
                "capabilities": list(config.capabilities),
                "auto_join": config.auto_join,
                "share_boundary": config.share_boundary(),
            },
            "config_path": str(self.store.config_path),
            "rooms_dir": str(self.store.rooms_dir),
            "peers": [peer.to_dict() for peer in config.peers],
            "room_count": len(rooms),
        }

    def capabilities(self) -> dict[str, Any]:
        config = self.store.load_config()
        return {
            "ok": True,
            "node_id": config.node_id,
            "display_name": config.display_name,
            "transport": config.transport,
            "capabilities": list(config.capabilities),
            "share_boundary": config.share_boundary(),
        }

    def open_room(self, *, topic: str, peer_ids: list[str], send_invites: bool = True) -> dict[str, Any]:
        config = self.store.load_config()
        room = self.store.create_room(topic=topic, participants=peer_ids, share_boundary=config.share_boundary())
        deliveries = []
        if send_invites:
            for peer_id in peer_ids:
                peer = config.peer_by_id(peer_id)
                if peer is None:
                    deliveries.append({"peer_id": peer_id, "ok": False, "error": "peer not configured"})
                    continue
                deliveries.append(self.send_invite(peer, room["room_id"]))
        return {"ok": True, "room": room, "deliveries": deliveries}

    def send_invite(self, peer: PeerNode, room_id: str) -> dict[str, Any]:
        if peer.peer_id:
            payload = self.store.invite_payload(room_id)
            return self._send_payload_via_netd(peer, payload, message_type="room_invite", room_id=room_id)
        if not peer.base_url:
            return {"peer_id": peer.node_id, "ok": False, "error": "peer has no libp2p peer_id or legacy base_url"}
        payload = self.store.invite_payload(room_id)
        prepared = self._prepare_outgoing_payload(peer, payload)
        if not prepared.get("ok"):
            return {"peer_id": peer.node_id, **prepared}
        result = self._post_json(f"{peer.base_url}/peer/rooms/invite", prepared["payload"])
        return {"peer_id": peer.node_id, "auth": prepared.get("auth", {}), **result}

    def receive_invite(self, payload: dict[str, Any], *, transport_auth: dict[str, Any] | None = None) -> dict[str, Any]:
        auth: dict[str, Any] = {}
        try:
            if transport_auth:
                auth = transport_auth
            else:
                payload, auth = self._unwrap_incoming_payload(payload)
            room = self.store.accept_invite(payload)
        except PeerAuthError as exc:
            return {"ok": False, "accepted": False, "error": str(exc), "auth": {"authenticated": False}}
        except PermissionError as exc:
            return {"ok": False, "accepted": False, "error": str(exc), "auth": auth}
        return {"ok": True, "accepted": True, "room": room, "auth": auth}

    def receive_message(self, payload: dict[str, Any], *, transport_auth: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            if transport_auth:
                auth = transport_auth
            else:
                payload, auth = self._unwrap_incoming_payload(payload)
        except PeerAuthError as exc:
            return {"ok": False, "error": str(exc), "auth": {"authenticated": False}}
        message = PeerMessage.from_payload(payload)
        if not message.room_id:
            return {"ok": False, "error": "room_id is required"}
        stored = self.store.append_message(message.room_id, message.to_record())
        return {"ok": True, "message": stored, "auth": auth}

    def append_message(
        self,
        *,
        room_id: str,
        from_node_id: str,
        content: str,
        to_node_ids: list[str] | None = None,
        message_type: str = "context_request",
        citations: list[str] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        message = PeerMessage(
            room_id=room_id,
            message_type=message_type,
            from_node_id=from_node_id,
            to_node_ids=tuple(to_node_ids or ()),
            content=content,
            citations=tuple(citations or ()),
            confidence=confidence,
        )
        return {"ok": True, "message": self.store.append_message(room_id, message.to_record())}

    def send_message_to_peer(
        self,
        *,
        peer_id: str,
        room_id: str,
        content: str,
        message_type: str = "context_request",
        citations: list[str] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        config = self.store.load_config()
        peer = config.peer_by_id(peer_id)
        if peer is None:
            return {"ok": False, "peer_id": peer_id, "error": "peer not configured"}
        message = PeerMessage(
            room_id=room_id,
            message_type=message_type,
            from_node_id=config.node_id,
            to_node_ids=(peer.node_id,),
            content=content,
            citations=tuple(citations or ()),
            confidence=confidence,
        )
        stored = self.store.append_message(room_id, message.to_record())
        payload = stored | {"room_id": room_id}
        delivery = self._send_payload_via_netd(peer, payload, message_type=message_type, room_id=room_id)
        return {"ok": bool(delivery.get("ok")), "message": stored, "delivery": delivery}

    def process_netd_inbox(self, limit: int | None = None) -> dict[str, Any]:
        client = self._netd()
        processed_ids = self.store.load_processed_netd_ids()
        results = []
        messages = client.messages()
        for envelope in messages[:limit]:
            envelope_id = _netd_envelope_id(envelope)
            if envelope_id in processed_ids:
                results.append({"ok": True, "skipped": True, "reason": "already_processed", "envelope_id": envelope_id})
                continue
            result = self.receive_netd_envelope(envelope)
            result["envelope_id"] = envelope_id
            self.store.mark_processed_netd_id(envelope_id)
            processed_ids.add(envelope_id)
            results.append(result)
        return {"ok": True, "count": len(results), "results": results}

    def receive_netd_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
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
            "payload_sha256": envelope.get("payload_sha256"),
        }
        if message_type == "room_invite":
            return self.receive_invite(payload, transport_auth=auth)
        payload.setdefault("type", message_type)
        payload.setdefault("room_id", message.get("room_id"))
        payload.setdefault("from_node_id", message.get("from_node_id"))
        payload.setdefault("to_node_ids", [message.get("to_node_id")] if message.get("to_node_id") else [])
        payload.setdefault("citations", message.get("citations") or [])
        payload.setdefault("created_at", message.get("created_at") or "")
        return self.receive_message(payload, transport_auth=auth)

    def list_rooms(self) -> dict[str, Any]:
        return {"ok": True, "rooms": self.store.list_rooms()}

    def room_detail(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room": self.store.get_room(room_id)}

    def context_pack(self, room_id: str, *, viewer_node_id: str | None = None) -> dict[str, Any]:
        return {"ok": True, "context": self.store.context_pack(room_id, viewer_node_id=viewer_node_id)}

    def update_summary(self, room_id: str, *, summary_md: str) -> dict[str, Any]:
        return {"ok": True, "message": self.store.update_rolling_summary(room_id, summary_md)}

    def _prepare_outgoing_payload(self, peer: PeerNode, payload: dict[str, Any]) -> dict[str, Any]:
        if not peer.shared_secret_env:
            return {"ok": True, "payload": payload, "auth": {"signed": False}}
        secret = secret_for_peer(peer)
        if not secret:
            return {
                "ok": False,
                "error": f"shared secret env is not set for peer {peer.node_id}: {peer.shared_secret_env}",
            }
        config = self.store.load_config()
        return {
            "ok": True,
            "payload": wrap_payload(payload=payload, from_node_id=config.node_id, secret=secret),
            "auth": {"signed": True, "algorithm": "hmac-sha256"},
        }

    def _unwrap_incoming_payload(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        return unwrap_payload(payload=payload, config=self.store.load_config())

    def _send_payload_via_netd(
        self,
        peer: PeerNode,
        payload: dict[str, Any],
        *,
        message_type: str,
        room_id: str,
    ) -> dict[str, Any]:
        if not peer.peer_id:
            return {"peer_id": peer.node_id, "ok": False, "error": "peer_id is required for libp2p send"}
        client = self._netd()
        connect_results = self._connect_peer_via_netd(peer, client)
        config = self.store.load_config()
        try:
            result = client.send_raw(
                peer.peer_id,
                {
                    "type": message_type,
                    "room_id": room_id,
                    "from_node_id": config.node_id,
                    "to_node_id": peer.node_id,
                    "payload": payload,
                    "citations": payload.get("citations") if isinstance(payload.get("citations"), list) else [],
                    "metadata": {
                        "to_node_id": peer.node_id,
                        "to_peer_id": peer.peer_id,
                        "transport": "libp2p",
                    },
                },
            )
        except (PeerNetdError, ValueError, TypeError) as exc:
            return {
                "peer_id": peer.node_id,
                "ok": False,
                "transport": "libp2p",
                "connect": connect_results,
                "error": str(exc),
            }
        return {
            "peer_id": peer.node_id,
            "ok": bool(result.get("ok", True)),
            "transport": "libp2p",
            "connect": connect_results,
            "response": result,
        }

    def _connect_peer_via_netd(self, peer: PeerNode, client: PeerNetdClient) -> list[dict[str, Any]]:
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

    def _netd(self) -> PeerNetdClient:
        if self.netd_client is not None:
            return self.netd_client
        return PeerNetdClient(base_url=os.getenv("AMO_PEER_NETD_URL", "http://127.0.0.1:8788"))

    def _post_json(self, url: str, payload: dict[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
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


def _netd_envelope_id(envelope: dict[str, Any]) -> str:
    signature = str(envelope.get("signature") or "").strip()
    if signature:
        return signature
    canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
