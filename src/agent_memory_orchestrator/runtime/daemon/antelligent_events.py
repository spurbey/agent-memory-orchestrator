from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from ...core.config import Settings
from ...peer.agent.state import PeerAgentStateStore
from ...peer.service import PeerService
from .antelligent_supervisor import antelligent_status

WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def antelligent_snapshot(settings: Settings, *, include_status: bool = False) -> dict[str, Any]:
    peer = PeerService(settings)
    state_store = PeerAgentStateStore(peer.store)
    rooms = []
    for room in peer.store.list_rooms():
        room_id = str(room.get("room_id") or "")
        if not room_id:
            continue
        try:
            detail = peer.store.get_room(room_id)
        except Exception:
            detail = room
        messages = detail.get("messages") if isinstance(detail.get("messages"), list) else []
        state = state_store.load(room_id)
        rooms.append(
            {
                "room_id": room_id,
                "topic": str(detail.get("topic") or ""),
                "initiator_node_id": str(detail.get("initiator_node_id") or ""),
                "participants": list(detail.get("participants") or []),
                "updated_at": str(detail.get("updated_at") or detail.get("created_at") or ""),
                "message_count": len(messages),
                "last_message_id": str(messages[-1].get("message_id") or "") if messages else "",
                "last_message_type": str(messages[-1].get("type") or "") if messages else "",
                "agent_status": str(state.get("status") or "open"),
                "finalized_reason": str(state.get("finalized_reason") or ""),
                "summary_version": int((state.get("summary") or {}).get("summary_version") or 0)
                if isinstance(state.get("summary"), dict)
                else 0,
            }
        )
    return {
        "status": antelligent_status(settings) if include_status else {},
        "rooms": rooms,
        "digest": _digest(rooms),
    }


def antelligent_events_since(
    *,
    settings: Settings,
    previous: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    current = antelligent_snapshot(settings, include_status=previous is None)
    if previous is None:
        return [_event("daemon_status", current["status"]), _event("worker_status", current["status"].get("worker", {}))], current
    prior_rooms = {str(room.get("room_id") or ""): room for room in previous.get("rooms", []) if isinstance(room, dict)}
    events: list[dict[str, Any]] = []
    for room in current.get("rooms", []):
        room_id = str(room.get("room_id") or "")
        prior = prior_rooms.get(room_id)
        if prior is None:
            events.append(_event("room_created", room))
            continue
        if room.get("message_count") != prior.get("message_count") or room.get("last_message_id") != prior.get("last_message_id"):
            events.append(_event("message_appended", room))
        if room.get("agent_status") != prior.get("agent_status"):
            event_type = "room_finalized" if room.get("agent_status") == "finalized" else "agent_state_updated"
            events.append(_event(event_type, room))
        elif room.get("finalized_reason") != prior.get("finalized_reason"):
            events.append(_event("agent_state_updated", room))
        if room.get("summary_version") != prior.get("summary_version"):
            events.append(_event("summary_updated", room))
        if room.get("updated_at") != prior.get("updated_at") and not any(
            event.get("payload", {}).get("room_id") == room_id for event in events
        ):
            events.append(_event("room_updated", room))
    if current.get("digest") != previous.get("digest") and not events:
        events.append(_event("room_updated", {"digest": current.get("digest")}))
    return events, current


def websocket_accept_key(sec_websocket_key: str) -> str:
    raw = hashlib.sha1((sec_websocket_key.strip() + WS_GUID).encode("ascii")).digest()
    return base64.b64encode(raw).decode("ascii")


def write_websocket_handshake(
    *,
    send_response: Callable[[int], None],
    send_header: Callable[[str, str], None],
    end_headers: Callable[[], None],
    headers: Mapping[str, Any],
) -> bool:
    key = str(headers.get("Sec-WebSocket-Key") or "").strip()
    if not key:
        send_response(400)
        send_header("Content-Type", "application/json")
        end_headers()
        return False
    send_response(101)
    send_header("Upgrade", "websocket")
    send_header("Connection", "Upgrade")
    send_header("Sec-WebSocket-Accept", websocket_accept_key(key))
    end_headers()
    return True


def websocket_text_frame(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    length = len(body)
    if length < 126:
        header = bytes([0x81, length])
    elif length <= 0xFFFF:
        header = bytes([0x81, 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([0x81, 127]) + length.to_bytes(8, "big")
    return header + body


def stream_antelligent_events(
    *,
    settings: Settings,
    write: Callable[[bytes], Any],
    flush: Callable[[], Any],
    interval_seconds: float = 1.0,
    max_iterations: int = 0,
) -> None:
    previous: dict[str, Any] | None = None
    iteration = 0
    while True:
        events, previous = antelligent_events_since(settings=settings, previous=previous)
        if not events and iteration % 15 == 0:
            events = [_event("heartbeat", {"ok": True})]
        for event in events:
            write(websocket_text_frame(event))
            flush()
        iteration += 1
        if max_iterations and iteration >= max_iterations:
            return
        time.sleep(max(0.1, interval_seconds))


def _event(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"type": event_type, "payload": payload, "created_at_ms": int(time.time() * 1000)}


def _digest(value: Any) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "antelligent_events_since",
    "antelligent_snapshot",
    "stream_antelligent_events",
    "websocket_accept_key",
    "websocket_text_frame",
    "write_websocket_handshake",
]
