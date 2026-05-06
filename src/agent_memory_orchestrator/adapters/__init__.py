from __future__ import annotations

from .base import NormalizedAdapterEvent, normalize_generic_event
from .claude import normalize_claude_event
from .codex import infer_codex_session, normalize_codex_event
from .omnara import normalize_omnara_event


def normalize_adapter_event(
    item: dict,
    *,
    default_agent: str = "system",
    default_session_id: str | None = None,
) -> dict[str, object] | None:
    """Normalize known provider/app payloads into the canonical event dict."""

    normalizers = (
        normalize_codex_event,
        normalize_claude_event,
        normalize_omnara_event,
        normalize_generic_event,
    )
    for normalizer in normalizers:
        event = normalizer(item, default_agent=default_agent, default_session_id=default_session_id)
        if event is not None:
            return event.as_dict()
    return None


__all__ = [
    "NormalizedAdapterEvent",
    "infer_codex_session",
    "normalize_adapter_event",
    "normalize_claude_event",
    "normalize_codex_event",
    "normalize_generic_event",
    "normalize_omnara_event",
]
