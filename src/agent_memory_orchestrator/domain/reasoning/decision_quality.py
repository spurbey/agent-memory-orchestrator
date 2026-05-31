from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .models import TimelineEvent


TOOL_ONLY_EVENT_TYPES = frozenset({"tool_use", "tool_result", "post_tool_use"})
WRITE_TOOL_NAMES = frozenset({"apply_patch"})


@dataclass(slots=True, frozen=True)
class DecisionQualityResult:
    ok: bool
    reason: str = ""
    fingerprint: str = ""


def qwen_decision_fingerprint(item: dict[str, Any]) -> str:
    """Build a stable semantic key for duplicate Qwen decisions."""

    parts = [
        _normalize_text(str(item.get("decision_type") or "")),
        _normalize_text(str(item.get("subject") or "")),
        _normalize_text(str(item.get("predicate") or "")),
        _normalize_text(str(item.get("object") or "")),
        _normalize_text(str(item.get("reason") or "")),
    ]
    return "|".join(parts)


def validate_qwen_decision_quality(
    item: dict[str, Any],
    *,
    event_by_id: dict[str, TimelineEvent],
    seen_fingerprints: set[str],
) -> DecisionQualityResult:
    """Apply graph-quality checks after schema/confidence validation.

    This gate is intentionally stricter than schema validation. Qwen may produce
    JSON that is formally valid but still graph-polluting, such as duplicate
    decisions or a planned action supported only by an applied patch event.
    """

    fingerprint = qwen_decision_fingerprint(item)
    if fingerprint in seen_fingerprints:
        return DecisionQualityResult(ok=False, reason="duplicate_decision", fingerprint=fingerprint)

    evidence_events = _qwen_evidence_events(item, event_by_id)
    decision_type = _normalize_text(str(item.get("decision_type") or ""))
    if decision_type == "planned_action" and evidence_events and all(event.event_type in TOOL_ONLY_EVENT_TYPES for event in evidence_events):
        return DecisionQualityResult(ok=False, reason="planned_action_tool_only_evidence", fingerprint=fingerprint)

    if decision_type == "planned_action" and any(_looks_like_write_event(event) for event in evidence_events):
        return DecisionQualityResult(ok=False, reason="write_patch_not_planned_action", fingerprint=fingerprint)

    return DecisionQualityResult(ok=True, fingerprint=fingerprint)


def _qwen_evidence_events(item: dict[str, Any], event_by_id: dict[str, TimelineEvent]) -> tuple[TimelineEvent, ...]:
    raw_ids = item.get("evidence_event_ids")
    if not isinstance(raw_ids, list):
        return ()
    return tuple(event_by_id[str(value)] for value in raw_ids if str(value) in event_by_id)


def _looks_like_write_event(event: TimelineEvent) -> bool:
    tool_name = _normalize_text(event.tool_name)
    if tool_name in WRITE_TOOL_NAMES:
        return True
    content = event.content.lower()
    return "*** begin patch" in content or "success. updated the following files" in content


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())
