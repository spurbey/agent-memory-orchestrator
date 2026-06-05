"""Daemon-local Antelligent companion routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ....core.config import Settings
from ....peer.agent import PeerAgentService
from ....peer.agent.state import PeerAgentStateStore
from ....peer.service import PeerService
from ..antelligent_auth import antelligent_auth_error, antelligent_auth_ok, antelligent_token_path
from ..antelligent_events import stream_antelligent_events, write_websocket_handshake
from ..antelligent_supervisor import antelligent_status

ANTELLIGENT_PREFIX = "/api/antelligent"
ANTELLIGENT_ROUTES = (
    "/api/antelligent/status",
    "/api/antelligent/events",
    "/api/antelligent/rooms",
    "/api/antelligent/chat",
)

JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_antelligent_get(
    *,
    path: str,
    query: dict[str, list[str]],
    headers: Mapping[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    if not path.startswith(ANTELLIGENT_PREFIX):
        return False
    if not antelligent_auth_ok(settings, headers=headers, query=query):
        write_json(401, antelligent_auth_error())
        return True
    if path == f"{ANTELLIGENT_PREFIX}/status":
        payload = antelligent_status(settings)
        # The desktop shell reads this token from disk; never echo the token value.
        payload["auth"] = {"token_path": str(antelligent_token_path(settings))}
        write_json(200, payload)
        return True
    if path == f"{ANTELLIGENT_PREFIX}/events":
        write_json(426, {"ok": False, "error": "websocket_upgrade_required"})
        return True
    room_route = _room_route(path)
    if room_route is None:
        write_json(404, {"ok": False, "error": "not found"})
        return True
    room_id, action = room_route
    service = PeerAgentService(settings)
    if not room_id and action == "list":
        write_json(200, _rooms_payload(settings))
        return True
    if action == "detail":
        write_json(200, _room_payload(settings, room_id))
        return True
    if action == "messages":
        write_json(200, service.messages(room_id))
        return True
    if action == "context":
        write_json(200, service.context(room_id))
        return True
    write_json(404, {"ok": False, "error": "not found"})
    return True


def handle_antelligent_post(
    *,
    path: str,
    payload: dict[str, Any],
    headers: Mapping[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    if not path.startswith(ANTELLIGENT_PREFIX):
        return False
    if not antelligent_auth_ok(settings, headers=headers):
        write_json(401, antelligent_auth_error())
        return True
    service = PeerAgentService(settings)
    if path == f"{ANTELLIGENT_PREFIX}/chat":
        result = service.ask(
            query=str(payload.get("query") or ""),
            peer_ids=_string_list(payload.get("peer_ids")),
            session_id=str(payload.get("session_id") or ""),
            min_confidence=payload.get("min_confidence"),
            timeout_seconds=payload.get("timeout_seconds"),
        )
        write_json(200 if result.get("ok") else 400, result)
        return True
    room_route = _room_route(path)
    if room_route is None:
        write_json(404, {"ok": False, "error": "not found"})
        return True
    room_id, action = room_route
    if action == "ask":
        result = service.ask_room(
            room_id=room_id,
            query=str(payload.get("query") or ""),
            peer_ids=_string_list(payload.get("peer_ids")),
            session_id=str(payload.get("session_id") or ""),
            min_confidence=payload.get("min_confidence"),
            timeout_seconds=payload.get("timeout_seconds"),
            wait_for_response=bool(payload.get("wait_for_response", False)),
        )
        write_json(200 if result.get("ok") else 400, result)
        return True
    if action == "continue":
        result = service.continue_room(
            room_id=room_id,
            min_confidence=payload.get("min_confidence"),
            timeout_seconds=payload.get("timeout_seconds"),
        )
        write_json(200 if result.get("ok") else 400, result)
        return True
    if action == "summarize":
        result = service.summarize(room_id)
        write_json(200 if result.get("ok") else 400, result)
        return True
    write_json(404, {"ok": False, "error": "not found"})
    return True


def handle_antelligent_websocket(
    *,
    path: str,
    query: dict[str, list[str]],
    headers: Mapping[str, Any],
    settings: Settings,
    handler: Any,
) -> bool:
    if path != f"{ANTELLIGENT_PREFIX}/events":
        return False
    if not antelligent_auth_ok(settings, headers=headers, query=query):
        handler._write_json(401, antelligent_auth_error())
        return True
    if str(headers.get("Upgrade") or "").lower() != "websocket":
        handler._write_json(426, {"ok": False, "error": "websocket_upgrade_required"})
        return True
    if not write_websocket_handshake(
        send_response=handler.send_response,
        send_header=handler.send_header,
        end_headers=handler.end_headers,
        headers=headers,
    ):
        return True
    try:
        stream_antelligent_events(settings=settings, write=handler.wfile.write, flush=handler.wfile.flush)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
        return True
    return True


def _rooms_payload(settings: Settings) -> dict[str, Any]:
    peer = PeerService(settings)
    state_store = PeerAgentStateStore(peer.store)
    rooms = []
    for room in peer.store.list_rooms():
        room_id = str(room.get("room_id") or "")
        row = dict(room)
        if room_id:
            row["agent_state"] = state_store.load(room_id)
        rooms.append(row)
    return {"ok": True, "rooms": rooms}


def _room_payload(settings: Settings, room_id: str) -> dict[str, Any]:
    peer = PeerService(settings)
    state_store = PeerAgentStateStore(peer.store)
    detail = peer.room_detail(room_id)
    if isinstance(detail.get("room"), dict):
        detail["agent_state"] = state_store.load(room_id)
    return detail


def _room_route(path: str) -> tuple[str, str] | None:
    if path == f"{ANTELLIGENT_PREFIX}/rooms":
        return "", "list"
    prefix = f"{ANTELLIGENT_PREFIX}/rooms/"
    if not path.startswith(prefix):
        return None
    parts = [part for part in path.removeprefix(prefix).split("/") if part]
    if not parts:
        return "", "list"
    if len(parts) == 1:
        return parts[0], "detail"
    if len(parts) == 2 and parts[1] in {"messages", "context", "ask", "continue", "summarize"}:
        return parts[0], parts[1]
    return None


def _string_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        out = [str(item).strip() for item in value if str(item).strip()]
        return out or None
    text = str(value).strip()
    return [text] if text else None


__all__ = [
    "ANTELLIGENT_ROUTES",
    "handle_antelligent_get",
    "handle_antelligent_post",
    "handle_antelligent_websocket",
]
