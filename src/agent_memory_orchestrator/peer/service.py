from __future__ import annotations

import json
from typing import Any
from urllib import request
from urllib.error import HTTPError, URLError

from ..core.config import Settings
from .models import PeerNode
from .store import PeerStore


class PeerService:
    def __init__(self, settings: Settings, store: PeerStore | None = None) -> None:
        self.settings = settings
        self.store = store or PeerStore(settings)

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
        base_url: str,
        display_name: str = "",
        capabilities: list[str] | None = None,
        trust: str = "trusted",
    ) -> dict[str, Any]:
        config = self.store.add_peer(
            PeerNode(
                node_id=node_id,
                base_url=base_url,
                display_name=display_name,
                capabilities=tuple(capabilities or ()),
                trust=trust,
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
        if not peer.base_url:
            return {"peer_id": peer.node_id, "ok": False, "error": "peer base_url is not configured"}
        payload = self.store.invite_payload(room_id)
        result = self._post_json(f"{peer.base_url}/peer/rooms/invite", payload)
        return {"peer_id": peer.node_id, **result}

    def receive_invite(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            room = self.store.accept_invite(payload)
        except PermissionError as exc:
            return {"ok": False, "accepted": False, "error": str(exc)}
        return {"ok": True, "accepted": True, "room": room}

    def receive_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        room_id = str(payload.get("room_id") or "").strip()
        if not room_id:
            return {"ok": False, "error": "room_id is required"}
        message = {
            "type": str(payload.get("type") or "peer_message"),
            "from": str(payload.get("from") or payload.get("from_node_id") or "").strip(),
            "to": payload.get("to") or payload.get("to_node_id") or "",
            "content": str(payload.get("content") or ""),
            "confidence": payload.get("confidence"),
            "citations": payload.get("citations") or [],
            "metadata": payload.get("metadata") or {},
        }
        stored = self.store.append_message(room_id, message)
        return {"ok": True, "message": stored}

    def list_rooms(self) -> dict[str, Any]:
        return {"ok": True, "rooms": self.store.list_rooms()}

    def room_detail(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room": self.store.get_room(room_id)}

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
