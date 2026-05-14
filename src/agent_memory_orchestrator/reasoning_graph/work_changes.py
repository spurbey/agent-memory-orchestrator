from __future__ import annotations

from typing import Any

from .models import DecisionUnit
from .validation import validate_graph_object


def work_changes_from_commit_windows(
    windows: list[dict[str, Any]],
    *,
    session_id: str,
    extraction_run_id: str,
) -> tuple[DecisionUnit, ...]:
    """Create deterministic work-change nodes from Git-verified commit windows."""

    changes: list[DecisionUnit] = []
    for window in windows:
        commit_id = str(window.get("commit_id") or "").strip()
        if not commit_id or not window.get("full_sha"):
            continue
        files = tuple(str(path) for path in window.get("git_changed_files", ()) if str(path).strip())
        summary = _summary_for_commit(window, files)
        change = DecisionUnit(
            id=f"work:{session_id}:{commit_id}",
            session_id=session_id,
            extraction_run_id=extraction_run_id,
            summary=summary,
            evidence_ids=(f"commit:{commit_id}",),
            kind="WorkChange",
            confidence=1.0,
            source="deterministic",
            metadata={
                "commit_id": commit_id,
                "full_sha": str(window.get("full_sha") or ""),
                "commit_message": str(window.get("message") or ""),
                "commit_category": _commit_category(str(window.get("message") or "")),
                "window_id": str(window.get("window_id") or ""),
                "ordinal": window.get("ordinal"),
                "start_index": window.get("start_index"),
                "end_index": window.get("end_index"),
                "event_count": window.get("event_count"),
                "message_event_count": window.get("message_event_count"),
                "tool_event_count": window.get("tool_event_count"),
                "git_changed_files": list(files),
                "git_name_status": window.get("git_name_status", ()),
            },
        )
        if validate_graph_object(change).ok:
            changes.append(change)
    return tuple(changes)


def _summary_for_commit(window: dict[str, Any], files: tuple[str, ...]) -> str:
    message = str(window.get("message") or "").strip() or str(window.get("commit_id") or "").strip()
    visible_files = ", ".join(files[:6])
    suffix = f" Changed {len(files)} files"
    if visible_files:
        suffix = f"{suffix}: {visible_files}"
        if len(files) > 6:
            suffix = f"{suffix}, +{len(files) - 6} more"
    return f"{message}.{suffix}."


def _commit_category(message: str) -> str:
    prefix = message.split(":", 1)[0].strip().lower()
    if "(" in prefix:
        prefix = prefix.split("(", 1)[0].strip()
    allowed = {"feat", "fix", "docs", "test", "refactor", "chore", "perf", "build", "ci"}
    return prefix if prefix in allowed else "change"
