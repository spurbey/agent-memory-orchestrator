from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..models import CodeHunk


HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
DIFF_FILE_RE = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")


def git_unified_zero_diff(repo_root: Path, commit: str, *, file_path: str = "") -> str:
    command = ["git", "show", "--unified=0", "--format=", commit]
    if file_path:
        command.extend(["--", file_path])
    result = subprocess.run(
        command,
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git show failed for {commit}")
    return result.stdout


def git_file_at_commit(repo_root: Path, commit: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{file_path}"],
        cwd=repo_root,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout


def parse_unified_zero_hunks(
    diff_text: str,
    *,
    session_id: str,
    extraction_run_id: str,
    commit_id: str,
    evidence_ids: tuple[str, ...],
) -> list[CodeHunk]:
    hunks: list[CodeHunk] = []
    current_file = ""
    current_header: re.Match[str] | None = None
    patch_lines: list[str] = []

    def flush() -> None:
        nonlocal current_header, patch_lines
        if current_header is None or not current_file:
            patch_lines = []
            return
        old_start = int(current_header.group("old_start"))
        old_count = _count_value(current_header.group("old_count"))
        new_start = int(current_header.group("new_start"))
        new_count = _count_value(current_header.group("new_count"))
        hunk_index = len(hunks) + 1
        hunks.append(
            CodeHunk(
                id=f"hunk:{commit_id}:{current_file}:{new_start}:{hunk_index}",
                session_id=session_id,
                extraction_run_id=extraction_run_id,
                file_path=current_file,
                old_start=old_start,
                old_count=old_count,
                new_start=new_start,
                new_count=new_count,
                patch="\n".join(patch_lines),
                commit_id=commit_id,
                evidence_ids=evidence_ids,
            )
        )
        current_header = None
        patch_lines = []

    for line in diff_text.splitlines():
        file_match = DIFF_FILE_RE.match(line)
        if file_match:
            flush()
            current_file = file_match.group("new")
            continue
        if line.startswith("+++ b/"):
            current_file = line.removeprefix("+++ b/")
            continue
        hunk_match = HUNK_RE.match(line)
        if hunk_match:
            flush()
            current_header = hunk_match
            patch_lines = [line]
            continue
        if current_header is not None:
            patch_lines.append(line)
    flush()
    return hunks


def _count_value(value: str | None) -> int:
    if value is None or value == "":
        return 1
    return int(value)



__all__ = ["git_file_at_commit", "git_unified_zero_diff", "parse_unified_zero_hunks"]
