from __future__ import annotations

import re
from pathlib import Path

from agent_memory_orchestrator.domain.semantic_harness import normalize_file_path

from .models import CapturedToolResult
from .models import ToolLineRef
from .models import ToolResultAnchors


PATH_SUFFIXES = {
    ".py",
    ".md",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".css",
    ".html",
    ".dart",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".swift",
    ".sh",
    ".ps1",
    ".sql",
}

ERROR_PATTERNS = (
    "traceback",
    "assertionerror",
    "exception",
    "error:",
    "failed",
    "failure",
    "syntaxerror",
    "typeerror",
    "valueerror",
)


def classify_tool_kind(captured: CapturedToolResult) -> str:
    tool_name = captured.tool_name.lower()
    command = captured.command.strip().lower()
    if "mcp" in tool_name or tool_name.startswith("mcp__"):
        return "mcp_result"
    if tool_name in {"apply_patch", "edit", "write"} or "apply_patch" in command:
        return "apply_patch"
    if _starts_with_any(command, ("rg ", "ripgrep ", "grep ", "select-string ")):
        return "search"
    if _starts_with_any(command, ("get-content ", "cat ", "type ")):
        return "file_read"
    if _starts_with_any(command, ("git diff", "git show", "git log")):
        return "git_diff"
    if "pytest" in command or "npm test" in command or "cargo test" in command or "go test" in command:
        return "test_output"
    return "unknown"


def extract_tool_result_anchors(captured: CapturedToolResult) -> ToolResultAnchors:
    tool_kind = classify_tool_kind(captured)
    command = captured.command
    response = captured.tool_response
    cwd = Path(captured.cwd).resolve() if captured.cwd else None
    files: list[str] = []
    symbols: list[str] = []
    errors: list[str] = []
    line_refs: list[ToolLineRef] = []

    if tool_kind == "search":
        line_refs.extend(_rg_line_refs(response, cwd=cwd))
        files.extend(ref.file_path for ref in line_refs)
        files.extend(_extract_paths(command, cwd=cwd))
    elif tool_kind == "file_read":
        files.extend(_extract_paths(command, cwd=cwd))
        symbols.extend(_python_symbols(response))
    elif tool_kind == "git_diff":
        files.extend(_git_changed_files(response, cwd=cwd))
        files.extend(_extract_paths(command, cwd=cwd))
    elif tool_kind == "test_output":
        files.extend(_pytest_files(response, cwd=cwd))
        line_refs.extend(_rg_line_refs(response, cwd=cwd))
        errors.extend(_error_lines(response))
    elif tool_kind == "apply_patch":
        files.extend(_patch_files(command + "\n" + response, cwd=cwd))
    elif tool_kind == "mcp_result":
        files.extend(_extract_paths(command + "\n" + response[:4000], cwd=cwd))
        errors.extend(_error_lines(response))
    else:
        files.extend(_extract_paths(command + "\n" + response[:4000], cwd=cwd))
        errors.extend(_error_lines(response))

    return ToolResultAnchors(
        files=tuple(_dedupe(files)[:16]),
        symbols=tuple(_dedupe(symbols)[:16]),
        errors=tuple(_dedupe(errors)[:8]),
        line_refs=tuple(_dedupe_line_refs(line_refs)[:24]),
    )


def _starts_with_any(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value.startswith(prefix) for prefix in prefixes)


def _rg_line_refs(text: str, *, cwd: Path | None) -> list[ToolLineRef]:
    refs: list[ToolLineRef] = []
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r"^(?P<path>(?![A-Za-z]:)[^:\r\n]+):(?P<line>\d+):(?P<text>.*)$", line)
        if not match:
            continue
        path = _normalize_candidate_path(match.group("path"), cwd=cwd)
        if not path:
            continue
        refs.append(ToolLineRef(file_path=path, line=int(match.group("line")), text=match.group("text").strip()[:240]))
    return refs


def _extract_paths(text: str, *, cwd: Path | None) -> list[str]:
    paths: list[str] = []
    patterns = (
        r"(?P<path>[A-Za-z]:[\\/][^\s'\"<>|]+)",
        r"(?P<path>(?:src|tests|docs|npm|scripts|agent-memory-orchestrator)[\\/][\w .@()\-\\/]+\.[A-Za-z0-9]+)",
        r"(?P<path>[\w .@()\-]+[\\/][\w .@()\-\\/]+\.[A-Za-z0-9]+)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            path = _normalize_candidate_path(match.group("path"), cwd=cwd)
            if path:
                paths.append(path)
    return paths


def _normalize_candidate_path(value: str, *, cwd: Path | None) -> str:
    cleaned = value.strip().strip("'\"`").rstrip(",:;)]}>\r\n")
    lowered = cleaned.lower()
    for prefix in ("get-content ", "cat ", "type ", "rg ", "ripgrep ", "grep ", "select-string "):
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            lowered = cleaned.lower()
    cleaned = cleaned.replace("\\", "/")
    if not cleaned:
        return ""
    try:
        path = Path(cleaned)
        if path.is_absolute() and cwd is not None:
            try:
                cleaned = str(path.resolve().relative_to(cwd)).replace("\\", "/")
            except ValueError:
                return ""
    except OSError:
        return ""
    if cleaned.startswith("../") or "/.git/" in cleaned or "/.codex/" in cleaned:
        return ""
    suffix = Path(cleaned).suffix.lower()
    if suffix not in PATH_SUFFIXES:
        return ""
    return normalize_file_path(cleaned)


def _python_symbols(text: str) -> list[str]:
    symbols: list[str] = []
    for match in re.finditer(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.MULTILINE):
        symbols.append(match.group(1))
    return symbols


def _git_changed_files(text: str, *, cwd: Path | None) -> list[str]:
    files: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", line)
        if match:
            files.append(_normalize_candidate_path(match.group(2), cwd=cwd))
            continue
        match = re.match(r"^(?:---|\+\+\+) [ab]/(.+)$", line)
        if match:
            files.append(_normalize_candidate_path(match.group(1), cwd=cwd))
    return [item for item in files if item]


def _pytest_files(text: str, *, cwd: Path | None) -> list[str]:
    files: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("FAILED "):
            candidate = line.removeprefix("FAILED ").split("::", 1)[0].split(" ", 1)[0]
            path = _normalize_candidate_path(candidate, cwd=cwd)
            if path:
                files.append(path)
        match = re.match(r"^(?P<path>(?:tests|src)[\\/][^:\s]+\.py):\d+:", line)
        if match:
            path = _normalize_candidate_path(match.group("path"), cwd=cwd)
            if path:
                files.append(path)
    return files


def _patch_files(text: str, *, cwd: Path | None) -> list[str]:
    files: list[str] = []
    for match in re.finditer(r"^\*\*\* (?:Add|Update|Delete) File:\s+(.+)$", text, flags=re.MULTILINE):
        path = _normalize_candidate_path(match.group(1), cwd=cwd)
        if path:
            files.append(path)
    return files


def _error_lines(text: str) -> list[str]:
    out: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(pattern in lowered for pattern in ERROR_PATTERNS):
            out.append(line[:260])
    return out


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _dedupe_line_refs(values: list[ToolLineRef]) -> list[ToolLineRef]:
    out: dict[tuple[str, int, str], ToolLineRef] = {}
    for value in values:
        out.setdefault((value.file_path, value.line, value.text), value)
    return list(out.values())


__all__ = ["classify_tool_kind", "extract_tool_result_anchors"]
