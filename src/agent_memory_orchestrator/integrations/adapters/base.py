from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


CANONICAL_AGENTS = {"claude", "codex", "user", "system"}
SKIP_GENERIC_TYPES = {"response_item", "event_msg", "turn_context", "compacted", "session_meta"}


@dataclass(slots=True, frozen=True)
class NormalizedAdapterEvent:
    session_id: str
    agent: str
    event_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: object | None = None
    source_app: str = "unknown"

    def as_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "agent": self.agent,
            "event_type": self.event_type,
            "content": self.content,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "source_app": self.source_app,
        }


def normalize_generic_event(
    item: dict,
    *,
    default_agent: str = "system",
    default_session_id: str | None = None,
) -> NormalizedAdapterEvent | None:
    if item.get("type") in SKIP_GENERIC_TYPES:
        return None

    event_name = str(item.get("hook_event_name") or item.get("event_type") or item.get("type") or "message")
    session_id = str(item.get("session_id") or item.get("sessionId") or default_session_id or "default")
    agent = normalize_agent(str(item.get("agent") or item.get("model_provider") or default_agent), default_agent)

    content = item.get("content") or item.get("message") or item.get("text")
    if content is None and event_name == "Stop":
        content = item.get("last_assistant_message") or ""
    if content is None and "tool" in item:
        content = item.get("tool")
    text = content_to_text(content or item)
    if not text.strip():
        return None

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = dict(metadata)
    for key in ("cwd", "turn_id", "transcript_path", "tool_name", "tool_input", "tool_output"):
        if key in item and key not in metadata:
            metadata[key] = item[key]

    return NormalizedAdapterEvent(
        session_id=session_id,
        agent=agent,
        event_type=snake(event_name),
        content=text,
        metadata=metadata,
        created_at=item.get("created_at") or item.get("timestamp"),
        source_app=agent if agent in {"claude", "codex"} else normalize_agent(default_agent, default_agent),
    )


def normalize_agent(agent: str, default_agent: str = "system") -> str:
    lowered = str(agent or default_agent or "system").lower()
    if "claude" in lowered or "anthropic" in lowered:
        return "claude"
    if "codex" in lowered or "openai" in lowered:
        return "codex"
    if lowered in CANONICAL_AGENTS:
        return lowered
    return default_agent if default_agent in CANONICAL_AGENTS else "system"


def snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or ""))
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def content_to_text(content: object) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(str(item.get("text") or ""))
                elif "content" in item:
                    parts.append(content_to_text(item.get("content")))
                else:
                    parts.append(compact_json(item))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part.strip())
    if isinstance(content, dict):
        return compact_json(content)
    return str(content)


def compact_json(value: object) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except TypeError:
        return str(value)
