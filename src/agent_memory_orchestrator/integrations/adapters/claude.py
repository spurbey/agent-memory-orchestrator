from __future__ import annotations

from typing import Any

from .base import NormalizedAdapterEvent, content_to_text, normalize_agent, snake


def normalize_claude_event(
    item: dict,
    *,
    default_agent: str = "system",
    default_session_id: str | None = None,
) -> NormalizedAdapterEvent | None:
    if not _looks_like_claude(item, default_agent):
        return None

    event_name = str(item.get("hook_event_name") or item.get("event_type") or item.get("type") or "message")
    session_id = str(
        item.get("session_id")
        or item.get("sessionId")
        or item.get("conversation_id")
        or default_session_id
        or "claude-session"
    )
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = {"adapter": "claude", **metadata}
    for key in ("cwd", "turn_id", "transcript_path", "tool_name", "tool_input", "tool_output"):
        if key in item and key not in metadata:
            metadata[key] = item[key]

    agent = _claude_agent(item, default_agent)
    content = _claude_content(item, event_name)
    text = content_to_text(content)
    if not text.strip():
        return None

    return NormalizedAdapterEvent(
        session_id=session_id,
        agent=agent,
        event_type=_claude_event_type(event_name, item, agent),
        content=text,
        metadata=metadata,
        created_at=item.get("created_at") or item.get("timestamp"),
        source_app="claude",
    )


def _looks_like_claude(item: dict, default_agent: str) -> bool:
    if default_agent == "claude":
        return True
    agent = str(item.get("agent") or item.get("model_provider") or item.get("source_app") or "").lower()
    if "claude" in agent or "anthropic" in agent:
        return True
    if item.get("transcript_path") and ".claude" in str(item.get("transcript_path")).lower():
        return True
    return False


def _claude_agent(item: dict, default_agent: str) -> str:
    role = ""
    message = item.get("message")
    if isinstance(message, dict):
        role = str(message.get("role") or "")
    role = role or str(item.get("role") or item.get("agent") or default_agent)
    normalized = normalize_agent(role, default_agent)
    if normalized == "system" and role.lower() == "assistant":
        return "claude"
    return normalized


def _claude_event_type(event_name: str, item: dict, agent: str) -> str:
    lowered = event_name.lower()
    if lowered in {"user", "human"} or agent == "user":
        return "prompt"
    if lowered in {"assistant", "message"} and agent == "claude":
        return "response"
    if "tool" in lowered:
        return snake(event_name)
    return snake(event_name)


def _claude_content(item: dict, event_name: str) -> Any:
    message = item.get("message")
    if isinstance(message, dict):
        return message.get("content") or message.get("text") or message
    content = item.get("content") or item.get("text") or item.get("message")
    if content is None and event_name == "Stop":
        content = item.get("last_assistant_message") or ""
    if content is None and "tool" in item:
        content = item.get("tool")
    return content
