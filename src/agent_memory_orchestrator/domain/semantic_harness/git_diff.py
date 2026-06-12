from __future__ import annotations

import re

from .hunk_mapping import CommitHunk
from .hunk_mapping import HunkRange
from .identity import normalize_file_path


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


def parse_unified_diff_hunks(diff_text: str) -> tuple[CommitHunk, ...]:
    """Parse Git unified diff text into deterministic CommitHunk inputs.

    The parser intentionally extracts only file path, ranges, and hunk text. It
    does not infer semantics and does not mutate graph state.
    """

    hunks: list[CommitHunk] = []
    current_path = ""
    pending_header: tuple[HunkRange, HunkRange] | None = None
    pending_lines: list[str] = []
    for raw_line in diff_text.splitlines():
        if match := _DIFF_HEADER_RE.match(raw_line):
            _flush_hunk(hunks, current_path, pending_header, pending_lines)
            pending_header = None
            pending_lines = []
            current_path = normalize_file_path(match.group(2))
            continue
        if raw_line.startswith("+++ "):
            next_path = _path_from_marker(raw_line[4:])
            if next_path:
                current_path = next_path
            continue
        if match := _HUNK_HEADER_RE.match(raw_line):
            _flush_hunk(hunks, current_path, pending_header, pending_lines)
            pending_header = (
                HunkRange(start=int(match.group("old_start")), count=int(match.group("old_count") or "1")),
                HunkRange(start=int(match.group("new_start")), count=int(match.group("new_count") or "1")),
            )
            pending_lines = [raw_line]
            continue
        if pending_header is not None:
            pending_lines.append(raw_line)
    _flush_hunk(hunks, current_path, pending_header, pending_lines)
    return tuple(hunks)


def _flush_hunk(
    hunks: list[CommitHunk],
    current_path: str,
    pending_header: tuple[HunkRange, HunkRange] | None,
    pending_lines: list[str],
) -> None:
    if pending_header is None or not current_path:
        return
    old_range, new_range = pending_header
    hunks.append(
        CommitHunk(
            file_path=current_path,
            old_range=old_range,
            new_range=new_range,
            text="\n".join(pending_lines),
        )
    )


def _path_from_marker(marker: str) -> str:
    value = marker.strip()
    if value == "/dev/null":
        return ""
    if value.startswith("a/") or value.startswith("b/"):
        value = value[2:]
    return normalize_file_path(value)


__all__ = ["parse_unified_diff_hunks"]
