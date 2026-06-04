from __future__ import annotations

from typing import Any

from .models import TriggerDecision


def detect_trigger(record: dict[str, Any]) -> TriggerDecision:
    _ = record
    return TriggerDecision(False, "none", "raw evidence pending until next session starts")


def session_boundary_trigger(previous_session_id: str, new_session_id: str) -> TriggerDecision:
    return TriggerDecision(
        True,
        "session_boundary",
        f"new session {new_session_id} started after session {previous_session_id}",
    )


def is_session_start(record: dict[str, Any]) -> bool:
    return _event_type(record) == "session_start"


def record_session_id(record: dict[str, Any]) -> str:
    payload = _payload(record)
    return str(record.get("session_id") or payload.get("session_id") or payload.get("sessionId") or "default")


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _event_type(record: dict[str, Any]) -> str:
    payload = _payload(record)
    raw = (
        str(record.get("event_name") or ""),
        str(payload.get("hook_event_name") or ""),
        str(payload.get("event_type") or ""),
    )
    return _snake(next((value for value in raw if value), ""))


def _snake(value: str) -> str:
    text = value.strip()
    out: list[str] = []
    prev_lower = False
    for char in text:
        if char.isupper() and prev_lower:
            out.append("_")
        if char.isalnum():
            out.append(char.lower())
            prev_lower = char.islower() or char.isdigit()
        else:
            if out and out[-1] != "_":
                out.append("_")
            prev_lower = False
    return "".join(out).strip("_")
