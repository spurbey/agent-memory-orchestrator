from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..store import PeerStore


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PeerAgentStateStore:
    def __init__(self, store: PeerStore) -> None:
        self.store = store

    def load(self, room_id: str) -> dict[str, Any]:
        path = self.path_for(room_id)
        if not path.exists():
            return self.default_state(room_id)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return self.default_state(room_id) | payload

    def save(self, room_id: str, state: dict[str, Any]) -> dict[str, Any]:
        payload = self.default_state(room_id) | dict(state)
        payload["updated_at"] = utc_now()
        path = self.path_for(room_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return payload

    def mark_processed_message(self, room_id: str, message_id: str) -> dict[str, Any]:
        state = self.load(room_id)
        return self._append_unique(room_id, state, "processed_message_ids", message_id)

    def mark_processed_request(self, room_id: str, request_id: str) -> dict[str, Any]:
        state = self.load(room_id)
        return self._append_unique(room_id, state, "processed_request_ids", request_id)

    def mark_response_sent(self, room_id: str, request_id: str) -> dict[str, Any]:
        state = self.load(room_id)
        return self._append_unique(room_id, state, "sent_response_for_request_ids", request_id)

    def record_response_attempt(self, room_id: str, request_id: str, attempt: dict[str, Any]) -> dict[str, Any]:
        request_id = str(request_id or "").strip()
        if not request_id:
            return self.load(room_id)
        state = self.load(room_id)
        attempts = state.get("response_attempts") if isinstance(state.get("response_attempts"), dict) else {}
        prior = attempts.get(request_id) if isinstance(attempts.get(request_id), dict) else {}
        attempts[request_id] = {
            "attempt_count": int(prior.get("attempt_count") or 0) + 1,
            "last_attempt_at": utc_now(),
            "last_ok": bool(attempt.get("ok")),
            "last_error": str((attempt.get("delivery") or {}).get("error") or attempt.get("error") or ""),
            "mode": str(attempt.get("mode") or prior.get("mode") or ""),
        }
        state["response_attempts"] = attempts
        return self.save(room_id, state)

    def has_processed_message(self, state: dict[str, Any], message_id: str) -> bool:
        return bool(message_id and message_id in set(_strings(state.get("processed_message_ids"))))

    def has_processed_request(self, state: dict[str, Any], request_id: str) -> bool:
        return bool(request_id and request_id in set(_strings(state.get("processed_request_ids"))))

    def has_sent_response(self, state: dict[str, Any], request_id: str) -> bool:
        return bool(request_id and request_id in set(_strings(state.get("sent_response_for_request_ids"))))

    def path_for(self, room_id: str) -> Path:
        safe = "".join(ch for ch in room_id.strip() if ch.isalnum() or ch in {"-", "_"})
        if not safe:
            raise ValueError("room_id is required")
        return self.store.rooms_dir / safe / "agent_state.json"

    def default_state(self, room_id: str) -> dict[str, Any]:
        return {
            "room_id": room_id,
            "schema_version": 1,
            "agent_managed": False,
            "status": "open",
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "original_query": "",
            "local_retrieval": {},
            "peer_requests": [],
            "processed_message_ids": [],
            "processed_request_ids": [],
            "sent_response_for_request_ids": [],
            "response_attempts": {},
            "deadline_at": "",
            "finalized_reason": "",
            "last_error": "",
            "summary": {
                "summary_version": 0,
                "summarized_until_message_id": "",
                "last_summary_at": "",
            },
            "final": {},
        }

    def _append_unique(self, room_id: str, state: dict[str, Any], key: str, value: str) -> dict[str, Any]:
        value = str(value or "").strip()
        if not value:
            return state
        items = _strings(state.get(key))
        if value not in items:
            items.append(value)
        state[key] = items
        return self.save(room_id, state)


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]
