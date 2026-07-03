from __future__ import annotations

from dataclasses import dataclass
from typing import Any


SUPPORTED_QUERY_MODES = (
    "context_for_anchor",
    "rank_tool_hits",
    "pre_edit_review",
    "relationship_between_anchors",
    "history_for_anchor",
    "semantic_diff",
)


@dataclass(slots=True, frozen=True)
class ModeCompatibilityResult:
    mode_requested: str
    mode_used: str
    source: str
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "mode_requested": self.mode_requested,
            "mode_used": self.mode_used,
            "source": self.source,
            "warnings": list(self.warnings),
        }


def resolve_query_mode(
    *,
    mode: str = "",
    intent: str = "",
    recent_tool_result: dict[str, Any] | None = None,
    planned_edits: tuple[object, ...] = (),
    questions: tuple[str, ...] = (),
) -> ModeCompatibilityResult:
    requested_mode = str(mode or "").strip()
    if requested_mode:
        if requested_mode in SUPPORTED_QUERY_MODES:
            return ModeCompatibilityResult(mode_requested=requested_mode, mode_used=requested_mode, source="mode")
        return ModeCompatibilityResult(
            mode_requested=requested_mode,
            mode_used="context_for_anchor",
            source="mode",
            warnings=(f"unsupported_mode:{requested_mode}",),
        )

    requested_intent = str(intent or "").strip() or "file_context"
    mapped = _mode_for_intent(
        requested_intent,
        recent_tool_result=recent_tool_result or {},
        planned_edits=planned_edits,
        questions=questions,
    )
    warnings: tuple[str, ...] = ()
    if mapped == "context_for_anchor" and requested_intent not in _KNOWN_INTENTS:
        warnings = (f"unsupported_intent:{requested_intent}",)
    return ModeCompatibilityResult(mode_requested=requested_intent, mode_used=mapped, source="intent", warnings=warnings)


def _mode_for_intent(
    intent: str,
    *,
    recent_tool_result: dict[str, Any],
    planned_edits: tuple[object, ...],
    questions: tuple[str, ...],
) -> str:
    if intent == "tool_overlay":
        return "rank_tool_hits" if _is_search_tool_result(recent_tool_result) else "context_for_anchor"
    if intent == "impact_check":
        return "pre_edit_review"
    if intent == "test_plan":
        return "pre_edit_review"
    if intent == "why_changed":
        return "history_for_anchor"
    if intent == "edit_plan":
        if planned_edits:
            return "pre_edit_review"
        if questions:
            return "context_for_anchor"
        return "context_for_anchor"
    if intent == "file_context":
        return "context_for_anchor"
    return "context_for_anchor"


def _is_search_tool_result(value: dict[str, Any]) -> bool:
    kind = str(value.get("kind") or value.get("tool_kind") or "").strip().lower()
    if kind in {"rg", "grep", "search"}:
        return True
    command = str(value.get("command") or "").strip().lower()
    return command.startswith(("rg ", "grep ", "select-string "))


_KNOWN_INTENTS = frozenset(
    {
        "edit_plan",
        "file_context",
        "tool_overlay",
        "impact_check",
        "test_plan",
        "why_changed",
    }
)


__all__ = ["ModeCompatibilityResult", "SUPPORTED_QUERY_MODES", "resolve_query_mode"]
