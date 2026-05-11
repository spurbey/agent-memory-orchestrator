from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from .models import CodeHunk
from .models import CodeNode


HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
DIFF_FILE_RE = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")

AstExpander = Callable[[CodeHunk, list[str]], tuple[str, int, int, str] | None]


def git_unified_zero_diff(repo_root: Path, commit: str, *, file_path: str = "") -> str:
    command = ["git", "show", "--unified=0", "--format=", commit]
    if file_path:
        command.extend(["--", file_path])
    result = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"git show failed for {commit}")
    return result.stdout


def git_file_at_commit(repo_root: Path, commit: str, file_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{commit}:{file_path}"],
        cwd=repo_root,
        text=True,
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


def code_nodes_from_hunks(
    hunks: list[CodeHunk],
    *,
    file_contents: dict[str, str],
    ast_expander: AstExpander | None = None,
) -> list[CodeNode]:
    nodes: list[CodeNode] = []
    for hunk in hunks:
        lines = file_contents.get(hunk.file_path, "").splitlines()
        expanded = ast_expander(hunk, lines) if ast_expander is not None else None
        if expanded is None:
            ast_type = "unparsed_hunk"
            start, end = _hunk_line_range(hunk, len(lines))
            content = _snippet(lines, start, end) or _patch_changed_lines(hunk.patch)
            ast_status = "unparsed"
        else:
            ast_type, start, end, content = expanded
            ast_status = "parsed"
            if not should_accept_ast_parent(max(1, hunk.new_count), max(1, end - start + 1)):
                start, end = _hunk_line_range(hunk, len(lines))
                content = _snippet(lines, start, end) or _patch_changed_lines(hunk.patch)
                ast_type = "unparsed_hunk"
                ast_status = "unparsed_parent_too_large"
        nodes.append(
            CodeNode(
                id=f"code:{hunk.commit_id}:{hunk.file_path}:{start}:{end}",
                session_id=hunk.session_id,
                extraction_run_id=hunk.extraction_run_id,
                file_path=hunk.file_path,
                ast_type=ast_type,
                line_start=start,
                line_end=end,
                content=content,
                commit_id=hunk.commit_id,
                evidence_ids=hunk.evidence_ids,
                ast_status=ast_status,
                metadata={"hunk_id": hunk.id},
            )
        )
    return nodes


def extract_code_nodes_from_commit(
    *,
    repo_root: Path,
    commit: str,
    session_id: str,
    extraction_run_id: str,
    evidence_ids: tuple[str, ...],
    file_path: str = "",
) -> tuple[list[CodeHunk], list[CodeNode]]:
    diff_text = git_unified_zero_diff(repo_root, commit, file_path=file_path)
    hunks = parse_unified_zero_hunks(
        diff_text,
        session_id=session_id,
        extraction_run_id=extraction_run_id,
        commit_id=commit,
        evidence_ids=evidence_ids,
    )
    contents = {hunk.file_path: git_file_at_commit(repo_root, commit, hunk.file_path) for hunk in hunks}
    return hunks, code_nodes_from_hunks(hunks, file_contents=contents)


def should_accept_ast_parent(hunk_size: int, parent_size: int) -> bool:
    return parent_size <= max(1, hunk_size) * 3


def _count_value(value: str | None) -> int:
    if value is None or value == "":
        return 1
    return int(value)


def _hunk_line_range(hunk: CodeHunk, total_lines: int) -> tuple[int, int]:
    start = max(1, hunk.new_start)
    count = max(1, hunk.new_count)
    end = min(max(start, total_lines), start + count - 1)
    return start, max(start, end)


def _snippet(lines: list[str], start: int, end: int) -> str:
    if not lines:
        return ""
    return "\n".join(lines[start - 1 : end]).strip()


def _patch_changed_lines(patch: str) -> str:
    out: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    if out:
        return "\n".join(out).strip()
    for line in patch.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            out.append(line[1:])
    return "\n".join(out).strip()
