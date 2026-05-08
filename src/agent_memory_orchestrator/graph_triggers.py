from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


WRITE_PATTERNS = (
    "apply_patch",
    "set-content",
    "add-content",
    "out-file",
    "new-item",
    "copy-item",
    "move-item",
    "remove-item",
    "write_text",
    "write_bytes",
)
TEST_PATTERNS = (
    "pytest",
    "ruff check",
    "npm test",
    "pnpm test",
    "yarn test",
    "cargo test",
    "go test",
    "flutter test",
)
GIT_PATTERNS = ("git status", "git diff", "git add", "git commit", "git show", "git log")
FINALIZE_PATTERNS = ("remember this", "save this decision", "this is final", "final decision", "finalize")


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


def detect_trigger(record: dict[str, Any], *, pending_write: bool = False) -> TriggerDecision:
    payload = _payload(record)
    event_name = str(record.get("event_name") or payload.get("hook_event_name") or "").lower()
    text = _record_text(record)

    if _contains_any(text, WRITE_PATTERNS):
        return TriggerDecision(True, "write", "write/edit tool detected", is_write=True)
    if _contains_any(text, GIT_PATTERNS):
        is_commit = "git commit" in text or bool(re.search(r"\[[^\]]+ [0-9a-f]{7,}\]", text))
        return TriggerDecision(True, "git_commit" if is_commit else "git", "git operation detected", is_git=True, is_commit=is_commit)
    if pending_write and _contains_any(text, TEST_PATTERNS):
        return TriggerDecision(True, "test", "test command after write detected", is_test=True)
    if event_name in {"stop", "session_stop"} and pending_write:
        return TriggerDecision(True, "stop_finalize", "session stopped with unsummarized writes")
    if event_name in {"userpromptsubmit", "user_prompt_submit", "prompt"} and _contains_any(text, FINALIZE_PATTERNS):
        return TriggerDecision(True, "explicit_finalize", "explicit memory/finalize request")
    return TriggerDecision(False, "none", "raw evidence only")


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


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)
