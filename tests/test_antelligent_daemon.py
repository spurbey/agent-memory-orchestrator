from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.service import PeerService
from agent_memory_orchestrator.peer.store import PeerStore
from agent_memory_orchestrator.runtime.daemon.antelligent_auth import ensure_antelligent_token
from agent_memory_orchestrator.runtime.daemon.antelligent_events import (
    antelligent_events_since,
    websocket_accept_key,
    websocket_text_frame,
)
from agent_memory_orchestrator.runtime.daemon.routes import antelligent as antelligent_routes


def test_antelligent_routes_require_local_token(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    writer = JsonWriter()

    handled = antelligent_routes.handle_antelligent_get(
        path="/api/antelligent/status",
        query={},
        headers={},
        settings=settings,
        write_json=writer.write,
    )

    assert handled is True
    assert writer.status == 401
    assert writer.payload["error"] == "antelligent_auth_required"


def test_antelligent_status_returns_readiness_without_token_leak(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    token = ensure_antelligent_token(settings)
    writer = JsonWriter()

    antelligent_routes.handle_antelligent_get(
        path="/api/antelligent/status",
        query={},
        headers=auth_headers(token),
        settings=settings,
        write_json=writer.write,
    )

    assert writer.status == 200
    assert writer.payload["ok"] is True
    assert writer.payload["daemon"]["ok"] is True
    assert writer.payload["worker"]["normal_worker"] == "peer-agent watch"
    assert token not in json.dumps(writer.payload)
    assert writer.payload["auth"]["token_path"].endswith("antelligent.token")


def test_antelligent_chat_route_wraps_peer_agent_service(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    token = ensure_antelligent_token(settings)
    captured: dict[str, Any] = {}

    class FakePeerAgent:
        def __init__(self, received_settings: Settings) -> None:
            captured["settings"] = received_settings

        def ask(self, **kwargs: Any) -> dict[str, Any]:
            captured["ask"] = kwargs
            return {"ok": True, "mode": "local_only", "answer": "local answer", "room_id": ""}

    monkeypatch.setattr(antelligent_routes, "PeerAgentService", FakePeerAgent)
    writer = JsonWriter()

    antelligent_routes.handle_antelligent_post(
        path="/api/antelligent/chat",
        payload={"query": "what did the designer change?", "timeout_seconds": 0},
        headers=auth_headers(token),
        settings=settings,
        write_json=writer.write,
    )

    assert writer.status == 200
    assert writer.payload["mode"] == "local_only"
    assert captured["ask"]["query"] == "what did the designer change?"
    assert captured["ask"]["timeout_seconds"] == 0


def test_antelligent_room_endpoints_return_messages_context_and_state(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    token = ensure_antelligent_token(settings)
    store = PeerStore(settings)
    store.init_config(node_id="host-amo")
    room = store.create_room(topic="responsive button work", participants=["designer-amo"])
    PeerService(settings, store=store).append_message(
        room_id=room["room_id"],
        from_node_id="host-amo",
        to_node_ids=["designer-amo"],
        content="What made the button responsive?",
        message_type="context_request",
        metadata={"audience": "peer", "request_id": "req_1"},
    )

    list_writer = JsonWriter()
    antelligent_routes.handle_antelligent_get(
        path="/api/antelligent/rooms",
        query={},
        headers=auth_headers(token),
        settings=settings,
        write_json=list_writer.write,
    )
    assert list_writer.payload["rooms"][0]["agent_state"]["status"] == "open"

    messages_writer = JsonWriter()
    antelligent_routes.handle_antelligent_get(
        path=f"/api/antelligent/rooms/{room['room_id']}/messages",
        query={},
        headers=auth_headers(token),
        settings=settings,
        write_json=messages_writer.write,
    )
    assert any(
        message["content"] == "What made the button responsive?"
        for message in messages_writer.payload["messages"]
    )

    context_writer = JsonWriter()
    antelligent_routes.handle_antelligent_get(
        path=f"/api/antelligent/rooms/{room['room_id']}/context",
        query={},
        headers=auth_headers(token),
        settings=settings,
        write_json=context_writer.write,
    )
    assert context_writer.payload["context"]["layers"]["room_md"]


def test_antelligent_events_detect_room_message_and_finalization(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = PeerStore(settings)
    store.init_config(node_id="host-amo")
    room = store.create_room(topic="agent room events", participants=["designer-amo"])

    initial_events, snapshot = antelligent_events_since(settings=settings, previous=None)
    assert [event["type"] for event in initial_events] == ["daemon_status", "worker_status"]

    PeerService(settings, store=store).append_message(
        room_id=room["room_id"],
        from_node_id="designer-amo",
        content="Designer found the responsive button memory.",
        message_type="context_response",
    )
    events, snapshot = antelligent_events_since(settings=settings, previous=snapshot)
    assert "message_appended" in {event["type"] for event in events}

    state_path = settings.home / ".peer" / "rooms" / room["room_id"] / "agent_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    state["status"] = "finalized"
    state["finalized_reason"] = "test"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    events, _ = antelligent_events_since(settings=settings, previous=snapshot)
    assert "room_finalized" in {event["type"] for event in events}


def test_antelligent_websocket_helpers_encode_accept_and_frame() -> None:
    assert websocket_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="
    frame = websocket_text_frame({"type": "heartbeat", "payload": {"ok": True}})
    assert frame[0] == 0x81
    assert b"heartbeat" in frame


class JsonWriter:
    def __init__(self) -> None:
        self.status = 0
        self.payload: dict[str, Any] = {}

    def write(self, status: int, payload: dict[str, Any]) -> bool:
        self.status = status
        self.payload = payload
        return True


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / ".data" / "agent_memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=16,
        embedding_model="hash-fallback",
        reranker_model="",
        vector_backend="sqlite",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=tmp_path / ".graph" / "amo.kuzu",
        retrieval_db_path=tmp_path / ".data" / "retrieval.sqlite",
        evidence_dir=tmp_path / ".evidence",
    )
