from __future__ import annotations

from .base import NormalizedAdapterEvent, content_to_text, normalize_agent, snake


def normalize_omnara_event(
    item: dict,
    *,
    default_agent: str = "system",
    default_session_id: str | None = None,
) -> NormalizedAdapterEvent | None:
    if not _looks_like_omnara(item):
        return None

    session_id = str(
        item.get("session_id")
        or item.get("task_id")
        or item.get("conversation_id")
        or default_session_id
        or "omnara-session"
    )
    event_name = str(item.get("event_type") or item.get("type") or item.get("status") or "visibility_event")
    agent = normalize_agent(str(item.get("agent") or item.get("actor") or default_agent), default_agent)
    content = item.get("content") or item.get("message") or item.get("summary") or item.get("status") or item
    text = content_to_text(content)
    if not text.strip():
        return None

    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    metadata = {
        "adapter": "omnara",
        "authoritative": False,
        "external_id": item.get("id") or item.get("external_id") or item.get("task_id"),
        **metadata,
    }
    return NormalizedAdapterEvent(
        session_id=session_id,
        agent=agent,
        event_type=snake(event_name),
        content=text,
        metadata=metadata,
        created_at=item.get("created_at") or item.get("timestamp"),
        source_app="omnara",
    )


def _looks_like_omnara(item: dict) -> bool:
    source = str(item.get("source_app") or item.get("source") or item.get("provider") or "").lower()
    if "omnara" in source:
        return True
    return bool(item.get("omnara_task_id") or item.get("omnara_session_id"))
