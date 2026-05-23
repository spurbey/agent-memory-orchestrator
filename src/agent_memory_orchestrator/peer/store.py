from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from ..core.config import Settings
from .context import build_context_pack
from .models import DEFAULT_CAPABILITIES, PeerConfig, PeerNode
from .policy import PeerPolicy


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _safe_room_id(room_id: str) -> str:
    clean = "".join(ch for ch in room_id.strip() if ch.isalnum() or ch in {"-", "_"})
    if not clean:
        raise ValueError("room_id is required")
    return clean


def _safe_peer_record_id(value: str) -> str:
    clean = "".join(ch for ch in value.strip() if ch.isalnum() or ch in {"-", "_"})
    if not clean:
        raise ValueError("record id is required")
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
        self.invites_dir = self.root / "invites"
        self.join_requests_dir = self.root / "join_requests"
        self.relay_profiles_path = self.root / "relay_profiles.json"
        self.netd_processed_path = self.root / "netd_processed.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.rooms_dir.mkdir(parents=True, exist_ok=True)
        self.invites_dir.mkdir(parents=True, exist_ok=True)
        self.join_requests_dir.mkdir(parents=True, exist_ok=True)

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

    def save_relay_profile(
        self,
        *,
        name: str,
        relay_addr: str,
        rendezvous_namespace: str,
        rendezvous_addr: str = "",
        auto_relay: bool = True,
        hole_punching: bool = True,
    ) -> dict[str, Any]:
        profile_name = _safe_peer_record_id(name)
        relay_addr = relay_addr.strip()
        rendezvous_addr = (rendezvous_addr or relay_addr).strip()
        rendezvous_namespace = rendezvous_namespace.strip()
        if not relay_addr:
            raise ValueError("relay_addr is required")
        if not rendezvous_addr:
            raise ValueError("rendezvous_addr is required")
        if not rendezvous_namespace:
            raise ValueError("rendezvous_namespace is required")
        profiles = self.load_relay_profiles()
        existing = profiles.get(profile_name, {})
        profile = {
            "name": profile_name,
            "relay_addr": relay_addr,
            "rendezvous_addr": rendezvous_addr,
            "rendezvous_namespace": rendezvous_namespace,
            "auto_relay": bool(auto_relay),
            "hole_punching": bool(hole_punching),
            "created_at": existing.get("created_at") or _utc_now(),
            "updated_at": _utc_now(),
        }
        profiles[profile_name] = profile
        self.relay_profiles_path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        return profile

    def load_relay_profiles(self) -> dict[str, dict[str, Any]]:
        if not self.relay_profiles_path.exists():
            return {}
        try:
            payload = json.loads(self.relay_profiles_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        profiles: dict[str, dict[str, Any]] = {}
        for key, value in payload.items():
            if isinstance(value, dict):
                profiles[str(key)] = value
        return profiles

    def list_relay_profiles(self) -> list[dict[str, Any]]:
        profiles = list(self.load_relay_profiles().values())
        profiles.sort(key=lambda item: str(item.get("name") or ""))
        return profiles

    def get_relay_profile(self, name: str) -> dict[str, Any]:
        profile_name = _safe_peer_record_id(name)
        profile = self.load_relay_profiles().get(profile_name)
        if not isinstance(profile, dict):
            raise FileNotFoundError(f"relay profile not found: {profile_name}")
        return profile

    def delete_relay_profile(self, name: str) -> dict[str, Any]:
        profile_name = _safe_peer_record_id(name)
        profiles = self.load_relay_profiles()
        removed = profiles.pop(profile_name, None)
        self.relay_profiles_path.write_text(json.dumps(profiles, indent=2), encoding="utf-8")
        return {"ok": True, "deleted": bool(removed), "name": profile_name}

    def save_peer_invite_record(self, record: dict[str, Any]) -> dict[str, Any]:
        invite_id = _safe_peer_record_id(str(record.get("invite_id") or ""))
        payload = dict(record)
        payload["invite_id"] = invite_id
        payload.setdefault("created_at", _utc_now())
        payload.setdefault("updated_at", _utc_now())
        payload.setdefault("status", "pending")
        payload.setdefault("used_count", 0)
        self._write_json(self.invites_dir / f"{invite_id}.json", payload)
        return payload

    def get_peer_invite_record(self, invite_id: str) -> dict[str, Any]:
        path = self.invites_dir / f"{_safe_peer_record_id(invite_id)}.json"
        if not path.exists():
            raise FileNotFoundError(f"peer invite not found: {invite_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"peer invite must be an object: {path}")
        return payload

    def update_peer_invite_record(self, invite_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        record = self.get_peer_invite_record(invite_id)
        record.update(updates)
        record["updated_at"] = _utc_now()
        return self.save_peer_invite_record(record)

    def save_join_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            invite_id = str(request.get("invite_id") or "invite").strip() or "invite"
            node_id = str((request.get("peer_card") or {}).get("node_id") or "peer").strip() or "peer"
            request_id = f"join_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{_sha256(invite_id + ':' + node_id)[:8]}"
        request_id = _safe_peer_record_id(request_id)
        payload = dict(request)
        payload["request_id"] = request_id
        payload.setdefault("created_at", _utc_now())
        payload.setdefault("updated_at", _utc_now())
        payload.setdefault("status", "pending")
        self._write_json(self.join_requests_dir / f"{request_id}.json", payload)
        return payload

    def get_join_request(self, request_id: str) -> dict[str, Any]:
        path = self.join_requests_dir / f"{_safe_peer_record_id(request_id)}.json"
        if not path.exists():
            raise FileNotFoundError(f"peer join request not found: {request_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"peer join request must be an object: {path}")
        return payload

    def update_join_request(self, request_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        request = self.get_join_request(request_id)
        request.update(updates)
        request["updated_at"] = _utc_now()
        return self.save_join_request(request)

    def list_join_requests(self, status: str = "") -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(self.join_requests_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            if status and payload.get("status") != status:
                continue
            rows.append(payload)
        rows.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
        return rows

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
        room_id = f"room_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
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

    def load_processed_netd_ids(self) -> set[str]:
        if not self.netd_processed_path.exists():
            return set()
        try:
            payload = json.loads(self.netd_processed_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return set()
        if not isinstance(payload, list):
            return set()
        return {str(item) for item in payload if str(item).strip()}

    def mark_processed_netd_id(self, envelope_id: str) -> None:
        envelope_id = envelope_id.strip()
        if not envelope_id:
            return
        processed = self.load_processed_netd_ids()
        processed.add(envelope_id)
        self.netd_processed_path.write_text(json.dumps(sorted(processed), indent=2), encoding="utf-8")

    def _write_json(self, path: Any, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

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
            "- Layer 3A: compact group-visible room exchanges.\n"
            "- Layer 3B: tagged initiator-peer exchanges for the active peer.\n"
            "- Peers auto-respond only when tagged by the initiator.\n\n"
            "## Desired Output\n"
            "Return useful local-memory findings with confidence and citations. Do not share raw evidence unless policy allows it.\n"
        )
