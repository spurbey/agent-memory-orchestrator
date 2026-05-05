from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True, frozen=True)
class CleanedEventText:
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


_REQUEST_MARKER_RE = re.compile(r"(?is)##\s*My request for (?:Codex|Claude)\s*:\s*")
_IDE_CONTEXT_RE = re.compile(r"(?is)#\s*Context from my IDE setup\s*:")
_LOW_VALUE_TOOL_PATTERNS = (
    "rg \\\"^# cell",
    "rg '^# cell",
    "get-childitem",
    "select-string",
    "format-table",
    "open tabs:",
    "active file:",
)
_DIAGNOSTIC_TOOL_PATTERNS = (
    "agent_memory_orchestrator.cli search",
    "agent_memory_orchestrator.cli metrics",
    "agent_memory_orchestrator.cli timeline",
    "agent_memory_orchestrator.cli retrieval",
    "agent_memory_orchestrator.cli session-summary",
    "python -m agent_memory_orchestrator.cli search",
)
_TOOL_SIGNAL_TERMS = (
    "all tests pass",
    "build failed",
    "build succeeded",
    "changed_files",
    "error",
    "exception",
    "failed",
    "fixed",
    "implemented",
    "patch applied",
    "passed",
    "pytest",
    "resolved",
    "success. updated",
    "test failed",
    "traceback",
    "updated the following files",
)
_USER_MEMORY_TERMS = (
    "approved",
    "do that",
    "final decision",
    "go with",
    "i decided",
    "make sure",
    "must",
    "remember",
    "use this",
    "we decided",
    "we will",
    "yes do",
)


def clean_event_text(
    text: str,
    *,
    event_type: str,
    agent: str,
    metadata: dict[str, Any] | None = None,
) -> CleanedEventText:
    """Prepare event text for chunking without mutating the raw event evidence."""
    raw = str(text or "")
    meta = dict(metadata or {})
    cleaned = raw.strip()
    cleaning: dict[str, Any] = {
        "original_chars": len(raw),
        "cleaned_chars": len(cleaned),
        "removed_ide_context": False,
        "promote_to_memory": True,
        "suppression_reason": "",
    }

    cleaned, ide_meta = _strip_ide_context(cleaned)
    if ide_meta:
        cleaning.update(ide_meta)

    cleaned = _strip_transient_markdown(cleaned)
    cleaning["cleaned_chars"] = len(cleaned)

    promote, reason = should_promote_to_memory(
        cleaned,
        event_type=event_type,
        agent=agent,
        metadata=meta,
    )
    cleaning["promote_to_memory"] = promote
    cleaning["suppression_reason"] = reason

    meta["amo_cleaning"] = cleaning
    meta["amo_promote_memory"] = promote
    if reason:
        meta["amo_suppression_reason"] = reason
    return CleanedEventText(text=cleaned, metadata=meta)


def should_promote_to_memory(
    text: str,
    *,
    event_type: str,
    agent: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    clean = " ".join(str(text or "").split())
    lowered = clean.lower()
    normalized_event = event_type.lower()
    normalized_agent = agent.lower()
    meta = metadata or {}

    if not clean:
        return False, "empty_after_cleanup"
    if normalized_event in {"session_meta", "turn_context"}:
        return False, "transient_session_context"
    if normalized_agent == "user" or normalized_event in {"prompt", "user_prompt_submit"}:
        if _is_diagnostic_paste(lowered):
            return False, "diagnostic_paste"
        if any(term in lowered for term in _USER_MEMORY_TERMS):
            return True, ""
        return False, "user_prompt_not_durable"
    if normalized_event in {"tool_result", "tool_output", "post_tool_use"}:
        tool_name = str(meta.get("tool_name") or meta.get("tool") or "").lower()
        if _is_diagnostic_tool_output(lowered):
            return False, "diagnostic_tool_output"
        if tool_name in {"read", "ls", "grep", "rg"} and not _has_tool_signal(lowered):
            return False, "low_value_read_only_tool_output"
        if _is_low_value_tool_output(lowered) and not _has_tool_signal(lowered):
            return False, "low_value_tool_output"
        if not _has_tool_signal(lowered):
            return False, "tool_output_without_durable_signal"
    if _is_raw_ide_context_only(lowered):
        return False, "ide_context_only"
    return True, ""


def _strip_ide_context(text: str) -> tuple[str, dict[str, Any]]:
    if not _IDE_CONTEXT_RE.search(text):
        return text.strip(), {}

    match = _REQUEST_MARKER_RE.search(text)
    if not match:
        return "", {
            "removed_ide_context": True,
            "ide_context_preview": _preview(text, 240),
            "suppression_reason": "ide_context_only",
        }

    prefix = text[: match.start()]
    request = text[match.end() :]
    return request.strip(), {
        "removed_ide_context": True,
        "ide_context_preview": _preview(prefix, 240),
    }


def _strip_transient_markdown(text: str) -> str:
    clean = text.strip()
    clean = re.sub(r"(?m)^<environment_context>.*?</environment_context>\s*", "", clean)
    clean = re.sub(r"(?m)^<turn_aborted>.*?</turn_aborted>\s*", "", clean)
    return clean.strip()


def _is_low_value_tool_output(lowered: str) -> bool:
    if "command completed:" not in lowered:
        return False
    return any(pattern in lowered for pattern in _LOW_VALUE_TOOL_PATTERNS)


def _is_diagnostic_tool_output(lowered: str) -> bool:
    if "command completed:" not in lowered:
        return False
    return any(pattern in lowered for pattern in _DIAGNOSTIC_TOOL_PATTERNS)


def _is_diagnostic_paste(lowered: str) -> bool:
    return (
        "python -m agent_memory_orchestrator.cli search" in lowered
        or "agent_memory_orchestrator.cli search" in lowered
        or ("\"ok\": true" in lowered and "\"results\"" in lowered)
        or ("ps c:\\" in lowered and "--query" in lowered)
    )


def _has_tool_signal(lowered: str) -> bool:
    return any(term in lowered for term in _TOOL_SIGNAL_TERMS)


def _is_raw_ide_context_only(lowered: str) -> bool:
    return "context from my ide setup" in lowered and "my request for" not in lowered


def _preview(text: str, max_len: int) -> str:
    clean = " ".join(str(text).split())
    return clean if len(clean) <= max_len else clean[: max_len - 3] + "..."
