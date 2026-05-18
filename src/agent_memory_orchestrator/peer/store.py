from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..core.config import Settings
from .context import build_context_pack
from .models import DEFAULT_CAPABILITIES, PeerConfig, PeerNode
from .policy import PeerPolicy


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_room_id(room_id: str) -> str:
    clean = "".join(ch for ch in room_id.strip() if ch.isalnum() or ch in {"-", "_"})
    if not clean:
        raise ValueError("room_id is required")
    return clean


class PeerStore:
    """Filesystem-backed peer room state.

    This is intentionally local and append-only for messages. Tailscale only
    provides reachability; AMO remains responsible for room state and policy.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.home / ".peer"
        self.config_path = self.root / "peers.json"
        self.rooms_dir = self.root / "rooms"
        self.root.mkdir(parents=True, exist_ok=True)
        self.rooms_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> PeerConfig:
        if not self.config_path.exists():
            return PeerConfig(node_id="local-amo")
        payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Peer config must be an object: {self.config_path}")
        return PeerConfig.from_dict(payload)

    def save_config(self, config: PeerConfig) -> PeerConfig:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
        return config

    def init_config(
        self,
        *,
        node_id: str,
        display_name: str = "",
        capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES,
    ) -> PeerConfig:
        config = self.load_config()
        updated = replace(
            config,
            node_id=node_id.strip() or config.node_id,
            display_name=display_name.strip() or config.display_name,
            capabilities=capabilities or config.capabilities,
        )
        return self.save_config(updated)

    def add_peer(self, peer: PeerNode) -> PeerConfig:
        if not peer.node_id:
            raise ValueError("peer node_id is required")
        if peer.base_url and not peer.base_url.startswith(("http://", "https://")):
            raise ValueError("peer base_url must be http:// or https://")
        if not any((peer.base_url, peer.peer_id, peer.multiaddrs, peer.relay_addrs, peer.rendezvous_addr)):
            raise ValueError("peer requires base_url, peer_id, multiaddrs, relay_addrs, or rendezvous_addr")
        config = self.load_config()
        return self.save_config(config.with_peer(peer))

    def list_rooms(self) -> list[dict[str, Any]]:
        rooms: list[dict[str, Any]] = []
        for room_path in sorted(self.rooms_dir.glob("*/room.json")):
            try:
                room = json.loads(room_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(room, dict):
                rooms.append(room)
        rooms.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return rooms

    def get_room(self, room_id: str) -> dict[str, Any]:
        room_id = _safe_room_id(room_id)
        room_path = self.rooms_dir / room_id / "room.json"
        if not room_path.exists():
            raise FileNotFoundError(f"peer room not found: {room_id}")
        room = json.loads(room_path.read_text(encoding="utf-8"))
        if not isinstance(room, dict):
            raise ValueError(f"peer room must be an object: {room_path}")
        room["messages"] = self.read_messages(room_id)
        room["room_md"] = self.read_text(room_id, "room.md")
        room["rolling_summary_md"] = self.read_text(room_id, "rolling_summary.md")
        return room

    def create_room(
        self,
        *,
        topic: str,
        participants: list[str],
        initiator_node_id: str | None = None,
        share_boundary: str = "",
    ) -> dict[str, Any]:
        config = self.load_config()
        initiator = initiator_node_id or config.node_id
        participant_ids = sorted(set([initiator, *[item.strip() for item in participants if item.strip()]]))
        topic = topic.strip()
        if not topic:
            raise ValueError("topic is required")
        room_id = f"room_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        room_md = self.render_room_md(
            room_id=room_id,
            topic=topic,
            initiator=initiator,
            participants=participant_ids,
            share_boundary=share_boundary or config.share_boundary(),
        )
        room = {
            "room_id": room_id,
            "topic": topic,
            "initiator_node_id": initiator,
            "participants": participant_ids,
            "status": "created",
            "created_at": _utc_now(),
            "updated_at": _utc_now(),
            "room_md_version": 1,
            "room_md_sha256": _sha256(room_md),
            "share_boundary": share_boundary or config.share_boundary(),
            "transport": config.transport,
        }
        self.write_room(room, room_md=room_md)
        self.append_message(
            room_id,
            {
                "type": "room_created",
                "from": initiator,
                "to": participant_ids,
                "content": topic,
                "created_at": _utc_now(),
            },
        )
        return self.get_room(room_id)

    def accept_invite(self, invite: dict[str, Any]) -> dict[str, Any]:
        config = self.load_config()
        room_id = _safe_room_id(str(invite.get("room_id") or ""))
        initiator = str(invite.get("initiator_node_id") or invite.get("initiator") or "").strip()
        participants = [str(item).strip() for item in invite.get("participants", []) if str(item).strip()]
        room_md = str(invite.get("room_md") or "")
        expected_hash = str(invite.get("room_md_sha256") or "").strip()
        if room_md and expected_hash and _sha256(room_md) != expected_hash:
            raise ValueError("room_md_sha256 does not match room_md")
        decision = PeerPolicy(config).decide_invite(initiator)
        if not decision.allowed:
            raise PermissionError(decision.reason)
        room = {
            "room_id": room_id,
            "topic": str(invite.get("topic") or "").strip(),
            "initiator_node_id": initiator,
            "participants": sorted(set(participants + [config.node_id])),
            "status": "active",
            "created_at": str(invite.get("created_at") or _utc_now()),
            "updated_at": _utc_now(),
            "room_md_version": int(invite.get("room_md_version") or 1),
            "room_md_sha256": expected_hash or _sha256(room_md),
            "share_boundary": str(invite.get("share_boundary") or config.share_boundary()),
            "transport": str(invite.get("transport") or config.transport),
        }
        self.write_room(room, room_md=room_md or self.render_room_md(
            room_id=room_id,
            topic=room["topic"],
            initiator=initiator,
            participants=room["participants"],
            share_boundary=room["share_boundary"],
        ))
        self.append_message(
            room_id,
            {
                "type": "room_invite_received",
                "from": initiator,
                "to": config.node_id,
                "content": room["topic"],
                "created_at": _utc_now(),
            },
        )
        return self.get_room(room_id)

    def context_pack(self, room_id: str, *, viewer_node_id: str | None = None) -> dict[str, Any]:
        config = self.load_config()
        room = self.get_room(room_id)
        viewer = viewer_node_id or config.node_id
        return build_context_pack(room=room, viewer_node_id=viewer, config=config).to_dict()

    def update_rolling_summary(self, room_id: str, summary_md: str) -> dict[str, Any]:
        room_id = _safe_room_id(room_id)
        room_dir = self.rooms_dir / room_id
        if not room_dir.exists():
            raise FileNotFoundError(f"peer room not found: {room_id}")
        summary_md = summary_md.strip()
        if not summary_md.startswith("#"):
            summary_md = "# Rolling Summary\n\n" + summary_md
        (room_dir / "rolling_summary.md").write_text(summary_md.strip() + "\n", encoding="utf-8")
        return self.append_message(
            room_id,
            {
                "type": "summary_update",
                "from": self.load_config().node_id,
                "content": "Rolling summary updated.",
            },
        )

    def invite_payload(self, room_id: str) -> dict[str, Any]:
        room = self.get_room(room_id)
        return {
            "type": "room_invite",
            "room_id": room["room_id"],
            "topic": room["topic"],
            "initiator_node_id": room["initiator_node_id"],
            "participants": room["participants"],
            "room_md": room["room_md"],
            "room_md_sha256": room["room_md_sha256"],
            "room_md_version": room["room_md_version"],
            "share_boundary": room.get("share_boundary", ""),
            "transport": room.get("transport", "libp2p"),
            "created_at": room["created_at"],
        }

    def write_room(self, room: dict[str, Any], *, room_md: str) -> None:
        room_id = _safe_room_id(str(room.get("room_id") or ""))
        room_dir = self.rooms_dir / room_id
        room_dir.mkdir(parents=True, exist_ok=True)
        (room_dir / "room.json").write_text(json.dumps(room, indent=2), encoding="utf-8")
        (room_dir / "room.md").write_text(room_md, encoding="utf-8")
        summary = room_dir / "rolling_summary.md"
        if not summary.exists():
            summary.write_text(
                "# Rolling Summary\n\n## Current Understanding\n\n- Pending peer responses.\n\n"
                "## Open Questions\n\n- Pending.\n",
                encoding="utf-8",
            )

    def append_message(self, room_id: str, message: dict[str, Any]) -> dict[str, Any]:
        room_id = _safe_room_id(room_id)
        room_dir = self.rooms_dir / room_id
        if not room_dir.exists():
            raise FileNotFoundError(f"peer room not found: {room_id}")
        payload = dict(message)
        payload.setdefault("message_id", f"msg_{uuid4().hex}")
        payload.setdefault("created_at", _utc_now())
        with (room_dir / "transcript.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        room_path = room_dir / "room.json"
        room = json.loads(room_path.read_text(encoding="utf-8"))
        room["updated_at"] = payload["created_at"]
        room_path.write_text(json.dumps(room, indent=2), encoding="utf-8")
        return payload

    def read_messages(self, room_id: str) -> list[dict[str, Any]]:
        room_id = _safe_room_id(room_id)
        path = self.rooms_dir / room_id / "transcript.jsonl"
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                rows.append(item)
        return rows

    def read_text(self, room_id: str, name: str) -> str:
        path = self.rooms_dir / _safe_room_id(room_id) / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def render_room_md(
        self,
        *,
        room_id: str,
        topic: str,
        initiator: str,
        participants: list[str],
        share_boundary: str,
    ) -> str:
        participant_lines = "\n".join(f"- {participant}" for participant in participants)
        return (
            "# AMO Peer Investigation Room\n\n"
            f"room_id: {room_id}\n"
            f"initiator: {initiator}\n"
            f"created_at: {_utc_now()}\n\n"
            "## Topic\n"
            f"{topic}\n\n"
            "## Participants\n"
            f"{participant_lines}\n\n"
            "## Share Boundary\n"
            f"{share_boundary}\n\n"
            "## Context Window Contract\n"
            "- Layer 1: this room.md brief.\n"
            "- Layer 2: initiator-owned rolling_summary.md.\n"
            "- Layer 3: peer sees last 2 initiator-peer exchanges; initiator sees last 3 room conversations.\n\n"
            "## Desired Output\n"
            "Return useful local-memory findings with confidence and citations. Do not share raw evidence unless policy allows it.\n"
        )
