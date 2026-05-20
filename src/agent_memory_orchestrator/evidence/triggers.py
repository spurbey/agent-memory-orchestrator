from __future__ import annotations

import json
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
    approx_tokens: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "should_process": self.should_process,
            "trigger_type": self.trigger_type,
            "reason": self.reason,
            "is_write": self.is_write,
            "is_test": self.is_test,
            "is_git": self.is_git,
            "is_commit": self.is_commit,
            "approx_tokens": self.approx_tokens,
        }


def detect_trigger(
    record: dict[str, Any],
    *,
    pending_approx_tokens: int = 0,
    token_threshold: int = 0,
) -> TriggerDecision:
    safe_tokens = max(0, int(pending_approx_tokens))

    if token_threshold > 0 and safe_tokens >= token_threshold:
        return TriggerDecision(
            True,
            "token_threshold",
            f"pending evidence window reached {safe_tokens} approx tokens",
            approx_tokens=safe_tokens,
        )
    return TriggerDecision(False, "none", "raw evidence only", approx_tokens=safe_tokens)


def estimate_record_tokens(record: dict[str, Any]) -> int:
    """Cheap token estimate for daemon trigger thresholds.

    We only need a stable boundary signal here; exact tokenizer accounting would
    make hooks/drain depend on model packages and slow down background capture.
    """

    return max(1, len(_record_text(record)) // 4)


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _record_text(record: dict[str, Any]) -> str:
    payload = _payload(record)
    chunks: list[str] = [
        str(record.get("event_name") or ""),
        str(payload.get("hook_event_name") or ""),
        str(payload.get("tool") or ""),
        str(payload.get("tool_name") or ""),
        str(payload.get("prompt") or ""),
        str(payload.get("content") or ""),
        str(payload.get("message") or ""),
    ]
    for key in ("tool_input", "tool_response"):
        value = payload.get(key)
        if isinstance(value, str):
            chunks.append(value)
        elif value:
            chunks.append(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return "\n".join(chunks).lower()
