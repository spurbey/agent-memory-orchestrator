from __future__ import annotations

from ..models import CodeHunk


PYTHON_LARGE_ADD_HUNK_LINE_THRESHOLD = 80
PYTHON_MAX_EXPANSIONS_PER_HUNK = 24
PYTHON_MAX_MODULE_ASSIGNMENT_BLOCKS = 6
GENERIC_MAX_EXPANSIONS_PER_HUNK = 20
GENERIC_MAX_PARENT_LINES = 80
BRACE_CONTROL_KEYWORDS = {
    "catch",
    "do",
    "else",
    "finally",
    "for",
    "if",
    "switch",
    "try",
    "while",
    "with",
}


def should_accept_ast_parent(hunk_size: int, parent_size: int) -> bool:
    return parent_size <= max(1, hunk_size) * 3


def should_accept_generic_parent(hunk_size: int, parent_size: int) -> bool:
    return parent_size <= min(GENERIC_MAX_PARENT_LINES, max(12, max(1, hunk_size) * 8))

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

def _is_brace_language(path: str) -> bool:
    return path.endswith((".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".dart", ".java", ".kt", ".kts", ".go", ".rs", ".swift", ".cs", ".cpp", ".c", ".h", ".hpp"))


def _is_css_file(path: str) -> bool:
    return path.endswith((".css", ".scss", ".sass", ".less"))


def _is_markup_file(path: str) -> bool:
    return path.endswith((".html", ".htm", ".xml", ".svg", ".vue", ".svelte"))


def _is_structured_config_file(path: str) -> bool:
    return path.endswith((".json", ".jsonc", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".env", ".example"))


def _language_for_file(file_path: str) -> str:
    if file_path.endswith(".py"):
        return "python"
    if file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
        return "javascript"
    if file_path.endswith(".dart"):
        return "dart"
    if _is_css_file(file_path):
        return "css"
    if _is_markup_file(file_path):
        return "markup"
    if file_path.endswith(".md"):
        return "markdown"
    if file_path.endswith((".json", ".toml", ".yaml", ".yml", ".env", ".ini", ".cfg", ".example")):
        return "structured_text"
    return "unknown"


def _structural_id(file_path: str, ast_type: str, symbol_name: str, start: int, end: int) -> str:
    symbol = symbol_name or f"{start}-{end}"
    return f"{file_path}::{ast_type}:{symbol}:{start}-{end}"


__all__ = [
    "BRACE_CONTROL_KEYWORDS",
    "GENERIC_MAX_EXPANSIONS_PER_HUNK",
    "GENERIC_MAX_PARENT_LINES",
    "PYTHON_LARGE_ADD_HUNK_LINE_THRESHOLD",
    "PYTHON_MAX_EXPANSIONS_PER_HUNK",
    "PYTHON_MAX_MODULE_ASSIGNMENT_BLOCKS",
    "_hunk_line_range",
    "_is_brace_language",
    "_is_css_file",
    "_is_markup_file",
    "_is_structured_config_file",
    "_language_for_file",
    "_old_hunk_line_range",
    "_patch_changed_lines",
    "_patch_removed_lines",
    "_previous_snippet",
    "_snippet",
    "_structural_id",
    "should_accept_ast_parent",
    "should_accept_generic_parent",
]
