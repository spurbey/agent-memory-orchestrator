from __future__ import annotations

import ast
import re
import subprocess
from dataclasses import dataclass
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .models import CodeHunk
from .models import CodeNode


HUNK_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
DIFF_FILE_RE = re.compile(r"^diff --git a/(?P<old>.+?) b/(?P<new>.+)$")


@dataclass(slots=True, frozen=True)
class AstExpansion:
    ast_type: str
    line_start: int
    line_end: int
    content: str
    ast_status: str = "parsed"
    language: str = ""
    symbol_name: str = ""
    symbol_kind: str = ""
    structural_id: str = ""
    diagnostics: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


AstExpansionResult = AstExpansion | tuple[str, int, int, str] | list[AstExpansion] | tuple[AstExpansion, ...]
AstExpander = Callable[[CodeHunk, list[str]], AstExpansionResult | None]


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
    old_file_contents: dict[str, str] | None = None,
    ast_expander: AstExpander | None = None,
) -> list[CodeNode]:
    nodes: list[CodeNode] = []
    for hunk in hunks:
        lines = file_contents.get(hunk.file_path, "").splitlines()
        old_lines = (old_file_contents or {}).get(hunk.file_path, "").splitlines()
        expanded = _normalize_expansions(ast_expander(hunk, lines) if ast_expander is not None else None)
        if not expanded:
            start, end = _hunk_line_range(hunk, len(lines))
            expanded = (
                AstExpansion(
                    ast_type="unparsed_hunk",
                    line_start=start,
                    line_end=end,
                    content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
                    ast_status="unparsed",
                    diagnostics=("ast_expander_missing",),
                ),
            )
        for item in expanded:
            start, end = item.line_start, item.line_end
            content = item.content
            ast_type = item.ast_type
            ast_status = item.ast_status
            diagnostics = list(item.diagnostics)
            if not should_accept_ast_parent(max(1, hunk.new_count), max(1, end - start + 1)):
                start, end = _hunk_line_range(hunk, len(lines))
                content = _snippet(lines, start, end) or _patch_changed_lines(hunk.patch)
                ast_type = "unparsed_hunk"
                ast_status = "unparsed_parent_too_large"
                diagnostics.append("parent_size_gt_3x_hunk")
            prev_content = _previous_snippet(old_lines, hunk)
            metadata = {
                "hunk_id": hunk.id,
                "hunk_old_start": hunk.old_start,
                "hunk_old_count": hunk.old_count,
                "hunk_new_start": hunk.new_start,
                "hunk_new_count": hunk.new_count,
                "language": item.language or _language_for_file(hunk.file_path),
                "symbol_name": item.symbol_name,
                "symbol_kind": item.symbol_kind,
                "structural_id": item.structural_id
                or _structural_id(hunk.file_path, item.ast_type, item.symbol_name, start, end),
                "ast_diagnostics": diagnostics,
            }
            if item.metadata:
                metadata.update(item.metadata)
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
                    prev_content=prev_content,
                    commit_id=hunk.commit_id,
                    evidence_ids=hunk.evidence_ids,
                    ast_status=ast_status,
                    metadata=metadata,
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
    ast_expander: AstExpander | None = None,
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
    old_contents = {
        hunk.file_path: git_file_at_commit(repo_root, f"{commit}^", hunk.file_path)
        for hunk in hunks
    }
    expander = ast_expander if ast_expander is not None else default_ast_expander
    return hunks, code_nodes_from_hunks(
        hunks,
        file_contents=contents,
        old_file_contents=old_contents,
        ast_expander=expander,
    )


def default_ast_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    if hunk.file_path.endswith(".py"):
        return python_ast_expander(hunk, lines)
    return None


def python_ast_expander(hunk: CodeHunk, lines: list[str]) -> tuple[AstExpansion, ...] | None:
    source = "\n".join(lines)
    if not source.strip():
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        start, end = _hunk_line_range(hunk, len(lines))
        return (
            AstExpansion(
                ast_type="unparsed_hunk",
                line_start=start,
                line_end=end,
                content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
                ast_status="unparsed_syntax_error",
                language="python",
                diagnostics=(f"syntax_error:{exc.lineno or 0}",),
            ),
        )
    start, end = _hunk_line_range(hunk, len(lines))
    changed_lines = range(start, end + 1)
    selected: dict[tuple[str, int, int, str], AstExpansion] = {}
    meaningful = [_python_node_region(node, lines, hunk.file_path) for node in ast.walk(tree)]
    meaningful = [region for region in meaningful if region is not None]
    for line_no in changed_lines:
        containing = [
            region
            for region in meaningful
            if region.line_start <= line_no <= region.line_end
            and should_accept_ast_parent(max(1, hunk.new_count), max(1, region.line_end - region.line_start + 1))
        ]
        if not containing:
            continue
        best = min(containing, key=lambda region: (region.line_end - region.line_start, _node_priority(region.ast_type)))
        selected[(best.ast_type, best.line_start, best.line_end, best.symbol_name)] = best
    if selected:
        return tuple(selected.values())
    start, end = _hunk_line_range(hunk, len(lines))
    return (
        AstExpansion(
            ast_type="unparsed_hunk",
            line_start=start,
            line_end=end,
            content=_snippet(lines, start, end) or _patch_changed_lines(hunk.patch),
            ast_status="unparsed_no_small_ast_parent",
            language="python",
            diagnostics=("no_ast_parent_within_3x_hunk",),
        ),
    )


def should_accept_ast_parent(hunk_size: int, parent_size: int) -> bool:
    return parent_size <= max(1, hunk_size) * 3


def _normalize_expansions(expanded: AstExpansionResult | None) -> tuple[AstExpansion, ...]:
    if expanded is None:
        return ()
    if isinstance(expanded, AstExpansion):
        return (expanded,)
    if isinstance(expanded, tuple) and len(expanded) == 4 and isinstance(expanded[0], str):
        ast_type, start, end, content = expanded
        return (AstExpansion(ast_type=ast_type, line_start=int(start), line_end=int(end), content=str(content)),)
    return tuple(item for item in expanded if isinstance(item, AstExpansion))


def _count_value(value: str | None) -> int:
    if value is None or value == "":
        return 1
    return int(value)


def _hunk_line_range(hunk: CodeHunk, total_lines: int) -> tuple[int, int]:
    start = max(1, hunk.new_start)
    count = max(1, hunk.new_count)
    end = min(max(start, total_lines), start + count - 1)
    return start, max(start, end)


def _old_hunk_line_range(hunk: CodeHunk, total_lines: int) -> tuple[int, int]:
    start = max(1, hunk.old_start)
    count = max(1, hunk.old_count)
    end = min(max(start, total_lines), start + count - 1)
    return start, max(start, end)


def _snippet(lines: list[str], start: int, end: int) -> str:
    if not lines:
        return ""
    return "\n".join(lines[start - 1 : end]).strip()


def _previous_snippet(old_lines: list[str], hunk: CodeHunk) -> str:
    if not old_lines or hunk.old_count <= 0:
        return _patch_removed_lines(hunk.patch)
    start, end = _old_hunk_line_range(hunk, len(old_lines))
    return _snippet(old_lines, start, end) or _patch_removed_lines(hunk.patch)


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


def _patch_removed_lines(patch: str) -> str:
    out: list[str] = []
    for line in patch.splitlines():
        if line.startswith("-") and not line.startswith("---"):
            out.append(line[1:])
    return "\n".join(out).strip()


def _python_node_region(node: ast.AST, lines: list[str], file_path: str) -> AstExpansion | None:
    if not _is_meaningful_python_node(node):
        return None
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    symbol_name, symbol_kind = _python_symbol(node)
    ast_type = type(node).__name__
    return AstExpansion(
        ast_type=ast_type,
        line_start=start,
        line_end=end,
        content=_snippet(lines, start, end),
        language="python",
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        structural_id=_structural_id(file_path, ast_type, symbol_name, start, end),
        metadata={"python_ast_type": ast_type},
    )


def _is_meaningful_python_node(node: ast.AST) -> bool:
    return isinstance(
        node,
        (
            ast.AsyncFunctionDef,
            ast.FunctionDef,
            ast.ClassDef,
            ast.Assign,
            ast.AnnAssign,
            ast.AugAssign,
            ast.Import,
            ast.ImportFrom,
            ast.Raise,
            ast.Assert,
            ast.Return,
            ast.Expr,
            ast.If,
            ast.For,
            ast.AsyncFor,
            ast.While,
            ast.With,
            ast.AsyncWith,
            ast.Try,
        ),
    )


def _python_symbol(node: ast.AST) -> tuple[str, str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return node.name, "function"
    if isinstance(node, ast.ClassDef):
        return node.name, "class"
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]  # type: ignore[attr-defined]
        names = [_target_name(target) for target in targets]
        return ",".join(name for name in names if name), "assignment"
    if isinstance(node, ast.Import):
        return ",".join(alias.name for alias in node.names), "import"
    if isinstance(node, ast.ImportFrom):
        module = node.module or ""
        return ",".join(f"{module}.{alias.name}".strip(".") for alias in node.names), "import"
    return "", type(node).__name__.lower()


def _target_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _target_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    if isinstance(node, ast.Subscript):
        return _target_name(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        return ",".join(_target_name(item) for item in node.elts)
    return ""


def _node_priority(ast_type: str) -> int:
    priority = {
        "Assign": 0,
        "AnnAssign": 0,
        "AugAssign": 0,
        "Raise": 0,
        "Assert": 0,
        "Return": 0,
        "Expr": 1,
        "FunctionDef": 2,
        "AsyncFunctionDef": 2,
        "ClassDef": 3,
    }
    return priority.get(ast_type, 4)


def _language_for_file(file_path: str) -> str:
    if file_path.endswith(".py"):
        return "python"
    if file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
        return "javascript"
    if file_path.endswith(".dart"):
        return "dart"
    if file_path.endswith((".json", ".toml", ".yaml", ".yml", ".md")):
        return "structured_text"
    return "unknown"


def _structural_id(file_path: str, ast_type: str, symbol_name: str, start: int, end: int) -> str:
    symbol = symbol_name or f"{start}-{end}"
    return f"{file_path}::{ast_type}:{symbol}:{start}-{end}"
