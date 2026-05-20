from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True, frozen=True)
class TriggerDecision:
    should_process: bool
    trigger_type: str
    reason: str
    is_write: bool = False
    is_test: bool = False
    is_git: bool = False
    is_commit: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "should_process": self.should_process,
            "trigger_type": self.trigger_type,
            "reason": self.reason,
            "is_write": self.is_write,
            "is_test": self.is_test,
            "is_git": self.is_git,
            "is_commit": self.is_commit,
        }


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
