from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import TimelineEvent
from .timeline import TimelineGraph
from .tool_facts import ToolFact, tool_facts_from_events


MESSAGE_CHARS = 2_000
CONTEXT_RADIUS = 2


@dataclass(slots=True, frozen=True)
class WindowEvent:
    event_id: str
    event_type: str
    evidence_id: str
    timestamp: str
    source_app: str
    text: str
    keep_reasons: tuple[str, ...]
    files: tuple[str, ...] = ()
    tool_fact: ToolFact | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "evidence_id": self.evidence_id,
            "timestamp": self.timestamp,
            "source_app": self.source_app,
            "text": self.text,
            "keep_reasons": list(self.keep_reasons),
            "files": list(self.files),
            "tool_fact": self.tool_fact.as_dict() if self.tool_fact else None,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class CleanedEvidenceWindow:
    session_id: str
    target_files: tuple[str, ...]
    raw_event_count: int
    kept_event_count: int
    dropped_event_count: int
    window_events: tuple[WindowEvent, ...]
    tool_facts: tuple[ToolFact, ...]
    drop_reason_counts: dict[str, int]
    diagnostics: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "target_files": list(self.target_files),
            "raw_event_count": self.raw_event_count,
            "kept_event_count": self.kept_event_count,
            "dropped_event_count": self.dropped_event_count,
            "drop_reason_counts": self.drop_reason_counts,
            "diagnostics": list(self.diagnostics),
            "tool_facts": [fact.as_dict() for fact in self.tool_facts],
            "window_events": [event.as_dict() for event in self.window_events],
        }

    def chunk_input_text(self) -> str:
        return "\n".join(event.text for event in self.window_events if event.text)


def build_cleaned_evidence_window(
    timeline: TimelineGraph,
    *,
    target_files: Iterable[str] = (),
    context_radius: int = CONTEXT_RADIUS,
) -> CleanedEvidenceWindow:
    events = list(timeline.events)
    targets = _target_set(target_files)
    all_tool_facts = tool_facts_from_events(events)
    fact_by_event = {fact.event_id: fact for fact in all_tool_facts}

    keep_reasons: dict[str, list[str]] = {}
    drop_reasons: dict[str, str] = {}

    for event in events:
        fact = fact_by_event.get(event.id)
        reasons = _direct_keep_reasons(event, fact, targets)
        if reasons:
            keep_reasons[event.id] = reasons
        else:
            drop_reasons[event.id] = _drop_reason(event, fact, targets)

    _add_context_neighbors(events, keep_reasons, radius=context_radius)
    window_events: list[WindowEvent] = []
    kept_tool_facts: list[ToolFact] = []
    for event in events:
        reasons = keep_reasons.get(event.id)
        if not reasons:
            continue
        fact = fact_by_event.get(event.id)
        if fact is not None:
            kept_tool_facts.append(fact)
        window_events.append(_window_event(event, fact, tuple(reasons)))

    drop_reason_counts: dict[str, int] = {}
    for event in events:
        if event.id in keep_reasons:
            continue
        reason = drop_reasons.get(event.id, "not_selected")
        drop_reason_counts[reason] = drop_reason_counts.get(reason, 0) + 1

    diagnostics = _diagnostics(events, window_events, all_tool_facts, targets)
    return CleanedEvidenceWindow(
        session_id=timeline.session_id,
        target_files=tuple(sorted(targets)),
        raw_event_count=len(events),
        kept_event_count=len(window_events),
        dropped_event_count=len(events) - len(window_events),
        window_events=tuple(window_events),
        tool_facts=tuple(kept_tool_facts),
        drop_reason_counts=drop_reason_counts,
        diagnostics=diagnostics,
    )


def _direct_keep_reasons(event: TimelineEvent, fact: ToolFact | None, targets: set[str]) -> list[str]:
    reasons: list[str] = []
    if fact is not None:
        fact_paths = (*fact.paths, *fact.changed_files, *fact.inspected_files)
        target_hit = _paths_match_targets(fact_paths, targets)
        if target_hit and fact.changed_files:
            reasons.append("target_file_changed")
        elif target_hit and fact.inspected_files:
            reasons.append("target_file_inspected")
        elif target_hit:
            reasons.append("target_file_mentioned")
        if fact.tool_kind in {"write_patch", "git_status", "git_diff_or_show", "git_commit_or_ref"} and (
            target_hit or not targets
        ):
            reasons.append(f"tool_fact:{fact.tool_kind}")
        if fact.tool_kind == "test_or_lint" and fact.test_result:
            reasons.append(f"test_or_lint:{fact.test_result}")
        if fact.raw_only and target_hit:
            reasons.append("large_target_tool_output_compacted")
        return _dedupe(reasons)

    text = _clean_message(event.content)
    event_files = {_norm(path) for path in event.files}
    if targets and event_files.intersection(targets):
        reasons.append("message_mentions_target_file")
    if event.event_type in {"user_message", "user_prompt_submit", "agent_message", "stop"} and _mentions_target(text, targets):
        reasons.append("message_text_mentions_target")
    return _dedupe(reasons)


def _add_context_neighbors(events: list[TimelineEvent], keep_reasons: dict[str, list[str]], *, radius: int) -> None:
    kept_indexes = [index for index, event in enumerate(events) if event.id in keep_reasons]
    for index in kept_indexes:
        start = max(0, index - radius)
        end = min(len(events), index + radius + 1)
        for neighbor in events[start:end]:
            if neighbor.id in keep_reasons:
                continue
            if neighbor.event_type in {"user_message", "user_prompt_submit", "agent_message", "stop"}:
                keep_reasons[neighbor.id] = ["context_neighbor"]


def _window_event(event: TimelineEvent, fact: ToolFact | None, keep_reasons: tuple[str, ...]) -> WindowEvent:
    if fact is not None:
        text = fact.chunk_text()
        files = _dedupe((*fact.paths, *fact.changed_files, *fact.inspected_files))
    else:
        text = _clean_message(event.content)[:MESSAGE_CHARS]
        files = event.files
    return WindowEvent(
        event_id=event.id,
        event_type=event.event_type,
        evidence_id=event.evidence_id,
        timestamp=event.timestamp,
        source_app=event.source_app,
        text=text,
        keep_reasons=keep_reasons,
        files=files,
        tool_fact=fact,
        metadata={
            "original_content_chars": len(event.content or ""),
            "tool_name": event.tool_name,
        },
    )


def _drop_reason(event: TimelineEvent, fact: ToolFact | None, targets: set[str]) -> str:
    if fact is not None:
        if fact.raw_only:
            return "raw_only_unrelated_tool_output"
        if fact.tool_kind == "environment_check":
            return "environment_check_unrelated"
        if fact.tool_kind == "generic_tool":
            return "generic_tool_unrelated"
        return f"tool_unrelated:{fact.tool_kind}"
    if event.event_type in {"session_start", "stop"}:
        return "low_value_session_boundary"
    if event.event_type in {"user_message", "user_prompt_submit", "agent_message"}:
        return "message_unrelated_to_targets" if targets else "message_not_near_tool_evidence"
    return "not_selected"


def _diagnostics(
    events: list[TimelineEvent],
    window_events: list[WindowEvent],
    tool_facts: tuple[ToolFact, ...],
    targets: set[str],
) -> tuple[str, ...]:
    diagnostics: list[str] = []
    if targets and not any(set(_norm(path) for path in event.files).intersection(targets) for event in window_events):
        diagnostics.append("target_files_not_present_in_window_files")
    if not any(event.tool_fact and event.tool_fact.changed_files for event in window_events):
        diagnostics.append("window_has_no_changed_file_tool_fact")
    if not any(event.tool_fact and event.tool_fact.test_result for event in window_events):
        diagnostics.append("window_has_no_test_or_lint_fact")
    if len(window_events) > max(40, len(events) // 2):
        diagnostics.append("window_may_be_too_broad")
    if any(fact.raw_only and fact.semantic_payload for fact in tool_facts):
        diagnostics.append("raw_only_fact_marked_semantic_payload")
    return tuple(diagnostics)


def _mentions_target(text: str, targets: set[str]) -> bool:
    if not targets:
        return False
    normalized = _norm(text)
    return any(_path_matches_target(normalized, target) or target.rsplit("/", 1)[-1] in normalized for target in targets)


def _paths_match_targets(paths: Iterable[str], targets: set[str]) -> bool:
    if not targets:
        return False
    return any(_path_matches_target(_norm(path), target) for path in paths for target in targets)


def _path_matches_target(path: str, target: str) -> bool:
    if not path or not target:
        return False
    if path == target:
        return True
    return path.endswith(f"/{target}") or target.endswith(f"/{path}")


def _target_set(target_files: Iterable[str]) -> set[str]:
    return {_norm(path) for path in target_files if str(path).strip()}


def _clean_message(text: str) -> str:
    cleaned_lines: list[str] = []
    for line in str(text or "").replace("\r", "").splitlines():
        stripped = line.strip()
        if stripped.startswith("## Open tabs:"):
            break
        if stripped.startswith("# Context from my IDE setup:"):
            continue
        if stripped.startswith("## Active file:"):
            continue
        cleaned_lines.append(line)
    cleaned = "\n".join(cleaned_lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip()
        if not clean:
            continue
        key = _norm(clean)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return tuple(out)


def _norm(value: str) -> str:
    return str(value or "").replace("\\", "/").strip().strip('"').strip("'").lower()
