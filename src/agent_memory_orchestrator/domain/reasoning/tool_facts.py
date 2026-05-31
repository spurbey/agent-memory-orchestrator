from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from .models import TimelineEvent


HUGE_OUTPUT_CHARS = 12_000
SEARCH_OUTPUT_CHARS = 8_000
CHUNK_TEXT_CHARS = 1_500


@dataclass(slots=True, frozen=True)
class ToolFact:
    event_id: str
    session_id: str
    evidence_id: str
    timestamp: str
    tool_name: str
    tool_kind: str
    command_preview: str = ""
    output_preview: str = ""
    output_chars: int = 0
    raw_only: bool = False
    semantic_payload: bool = False
    paths: tuple[str, ...] = ()
    changed_files: tuple[str, ...] = ()
    inspected_files: tuple[str, ...] = ()
    test_result: str = ""
    keep_reasons: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "evidence_id": self.evidence_id,
            "timestamp": self.timestamp,
            "tool_name": self.tool_name,
            "tool_kind": self.tool_kind,
            "command_preview": self.command_preview,
            "output_preview": self.output_preview,
            "output_chars": self.output_chars,
            "raw_only": self.raw_only,
            "semantic_payload": self.semantic_payload,
            "paths": list(self.paths),
            "changed_files": list(self.changed_files),
            "inspected_files": list(self.inspected_files),
            "test_result": self.test_result,
            "keep_reasons": list(self.keep_reasons),
            "diagnostics": list(self.diagnostics),
            "metadata": self.metadata,
        }

    def chunk_text(self) -> str:
        parts = [
            f"ToolFact: {self.tool_kind}",
            f"tool={self.tool_name}" if self.tool_name else "",
            f"command={self.command_preview}" if self.command_preview else "",
            f"changed_files={', '.join(self.changed_files)}" if self.changed_files else "",
            f"inspected_files={', '.join(self.inspected_files[:8])}" if self.inspected_files else "",
            f"test_result={self.test_result}" if self.test_result else "",
            f"output_preview={self.output_preview}" if self.semantic_payload and self.output_preview else "",
            f"raw_only={self.raw_only}",
        ]
        text = " | ".join(part for part in parts if part)
        return text[:CHUNK_TEXT_CHARS]


def tool_facts_from_events(events: Iterable[TimelineEvent]) -> tuple[ToolFact, ...]:
    ordered = list(events)
    result_by_call_id: dict[str, TimelineEvent] = {}
    for event in ordered:
        if event.event_type != "tool_result":
            continue
        call_id = str(event.metadata.get("call_id") or "")
        if call_id and call_id not in result_by_call_id:
            result_by_call_id[call_id] = event

    facts: list[ToolFact] = []
    paired_result_ids: set[str] = set()
    for event in ordered:
        if event.event_type == "tool_use":
            call_id = str(event.metadata.get("call_id") or "")
            result = result_by_call_id.get(call_id)
            if result is not None:
                facts.append(_paired_tool_fact(event, result, call_id=call_id))
                paired_result_ids.add(result.id)
                continue
        if event.id in paired_result_ids:
            continue
        fact = tool_fact_from_event(event)
        if fact is not None:
            facts.append(fact)
    return tuple(facts)


def tool_fact_from_event(event: TimelineEvent) -> ToolFact | None:
    if event.event_type not in {"tool_use", "tool_result", "post_tool_use"}:
        return None

    command = _command_text(event)
    output = event.content or ""
    return _tool_fact_from_parts(event=event, command=command, output=output, files=event.files)


def _paired_tool_fact(tool_use: TimelineEvent, tool_result: TimelineEvent, *, call_id: str) -> ToolFact:
    command = _command_text(tool_use) or tool_use.content
    output = tool_result.content or ""
    files = _dedupe((*tool_use.files, *tool_result.files))
    fact = _tool_fact_from_parts(
        event=tool_use,
        command=command,
        output=output,
        files=files,
        metadata={
            "call_id": call_id,
            "paired_result_event_id": tool_result.id,
            "source_event_ids": [tool_use.id, tool_result.id],
            "raw_event_name": tool_use.metadata.get("raw_event_name", ""),
        },
    )
    return fact


def _tool_fact_from_parts(
    *,
    event: TimelineEvent,
    command: str,
    output: str,
    files: Iterable[str] = (),
    metadata: dict[str, Any] | None = None,
) -> ToolFact:
    paths = _dedupe((*files, *_extract_paths(command), *_extract_paths(output)))
    changed_files = _changed_files(event, command, output, paths)
    inspected_files = _inspected_files(command, output, paths, changed_files)
    tool_kind = _classify_tool(event, command, output, changed_files=changed_files, inspected_files=inspected_files)
    test_result = _test_result(command, output) if tool_kind == "test_or_lint" else ""
    output_chars = len(output)
    raw_only = output_chars > HUGE_OUTPUT_CHARS
    semantic_payload = False if raw_only else _semantic_payload(tool_kind, output_chars)
    keep_reasons = _keep_reasons(
        tool_kind=tool_kind,
        changed_files=changed_files,
        inspected_files=inspected_files,
        test_result=test_result,
        raw_only=raw_only,
        semantic_payload=semantic_payload,
    )
    diagnostics = _diagnostics(tool_kind=tool_kind, output=output, paths=paths, raw_only=raw_only)

    return ToolFact(
        event_id=event.id,
        session_id=event.session_id,
        evidence_id=event.evidence_id,
        timestamp=event.timestamp,
        tool_name=event.tool_name,
        tool_kind=tool_kind,
        command_preview=_one_line(command, 240),
        output_preview=_one_line(output, 360),
        output_chars=output_chars,
        raw_only=raw_only,
        semantic_payload=semantic_payload,
        paths=paths,
        changed_files=changed_files,
        inspected_files=inspected_files,
        test_result=test_result,
        keep_reasons=keep_reasons,
        diagnostics=diagnostics,
        metadata=metadata or {"raw_event_name": event.metadata.get("raw_event_name", "")},
    )


def _command_text(event: TimelineEvent) -> str:
    value = event.metadata.get("tool_input_text")
    if isinstance(value, str) and value.strip():
        return value.strip()
    if event.event_type == "tool_use":
        return event.content
    return ""


def _classify_tool(
    event: TimelineEvent,
    command: str,
    output: str,
    *,
    changed_files: tuple[str, ...],
    inspected_files: tuple[str, ...],
) -> str:
    tool = event.tool_name.lower()
    command_l = command.lower()
    output_l = output.lower()
    if tool == "apply_patch" or "*** begin patch" in command_l or "success. updated" in output_l:
        return "write_patch"
    if _git_subcommand(command_l) == "status":
        return "git_status"
    if _git_subcommand(command_l) in {"diff", "show"}:
        return "git_diff_or_show"
    if _git_subcommand(command_l) in {"commit", "rev-parse"}:
        return "git_commit_or_ref"
    if _looks_like_test_or_lint(command_l):
        return "test_or_lint"
    if _looks_like_filesystem_write(command_l):
        return "filesystem_write"
    if _looks_like_read_or_search(command_l):
        return "read_or_search"
    if _looks_like_environment_check(command_l):
        return "environment_check"
    if len(output) > HUGE_OUTPUT_CHARS and not changed_files and not inspected_files:
        return "large_diagnostic_output"
    return "generic_tool"


def _changed_files(event: TimelineEvent, command: str, output: str, paths: tuple[str, ...]) -> tuple[str, ...]:
    text = f"{command}\n{output}"
    changed: list[str] = []
    for line in text.replace("\\n", "\n").splitlines():
        stripped = line.strip().strip('"')
        if stripped.startswith(("M ", "A ", "D ", "R ", "C ", "?? ")):
            candidate = re.sub(r"^(?:M|A|D|R|C|\?\?)\s+", "", stripped).strip()
            if _looks_like_path(candidate):
                changed.append(candidate)
        if stripped.startswith("*** Update File: "):
            changed.append(stripped.removeprefix("*** Update File: ").strip())
        if stripped.startswith("*** Add File: "):
            changed.append(stripped.removeprefix("*** Add File: ").strip())
        if "Success. Updated the following files:" in stripped:
            continue
    if event.tool_name.lower() == "apply_patch" or "success. updated" in output.lower():
        changed.extend(paths)
    return _dedupe(changed)


def _inspected_files(
    command: str,
    output: str,
    paths: tuple[str, ...],
    changed_files: tuple[str, ...],
) -> tuple[str, ...]:
    command_l = command.lower()
    if not _looks_like_read_or_search(command_l):
        return ()
    changed = {_norm(path) for path in changed_files}
    return tuple(path for path in paths if _norm(path) not in changed)


def _semantic_payload(tool_kind: str, output_chars: int) -> bool:
    if tool_kind in {"write_patch", "git_status", "git_commit_or_ref", "test_or_lint"}:
        return True
    if tool_kind == "filesystem_write":
        return True
    if tool_kind == "git_diff_or_show":
        return output_chars <= 20_000
    if tool_kind == "read_or_search":
        return output_chars <= SEARCH_OUTPUT_CHARS
    return False


def _keep_reasons(
    *,
    tool_kind: str,
    changed_files: tuple[str, ...],
    inspected_files: tuple[str, ...],
    test_result: str,
    raw_only: bool,
    semantic_payload: bool,
) -> tuple[str, ...]:
    reasons: list[str] = [f"tool_kind:{tool_kind}"]
    if changed_files:
        reasons.append("changed_files")
    if inspected_files:
        reasons.append("inspected_files")
    if test_result:
        reasons.append(f"test_result:{test_result}")
    if raw_only:
        reasons.append("raw_only_large_output")
    if semantic_payload:
        reasons.append("semantic_payload")
    return tuple(reasons)


def _diagnostics(*, tool_kind: str, output: str, paths: tuple[str, ...], raw_only: bool) -> tuple[str, ...]:
    diagnostics: list[str] = []
    if raw_only:
        diagnostics.append("full_output_kept_in_raw_evidence_only")
    if tool_kind == "generic_tool" and paths:
        diagnostics.append("generic_tool_with_paths")
    if any(_looks_like_path_noise(path) for path in paths):
        diagnostics.append("possible_path_noise")
    return tuple(diagnostics)


def _test_result(command: str, output: str) -> str:
    text = f"{command}\n{output}".lower()
    fail_markers = (" failed", "failure", "traceback", "exit code: 1", "error:")
    pass_markers = (" passed", "all checks passed", "exit code: 0", "[100%]")
    failed = any(marker in text for marker in fail_markers)
    passed = any(marker in text for marker in pass_markers)
    if failed:
        return "fail"
    if passed:
        return "pass"
    return "unknown"


def _extract_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    normalized = str(text or "").replace("\\n", "\n")
    for line in normalized.splitlines():
        stripped = line.strip().strip('"').strip("'")
        for prefix in ("M ", "A ", "D ", "R ", "C ", "?? "):
            if stripped.startswith(prefix):
                candidate = stripped.removeprefix(prefix).strip()
                if _looks_like_path(candidate):
                    paths.append(candidate)
        for match in re.finditer(
            r"([A-Za-z]:\\[^\r\n\"<>|]+|(?:src|tests|docs|agent-memory-orchestrator|flutter|backend)[/\\][\w./\\-]+)",
            stripped,
        ):
            candidate = match.group(1).strip().rstrip(",:;)")
            if _looks_like_path(candidate):
                paths.append(candidate)
    return _dedupe(paths)


def _looks_like_path(value: str) -> bool:
    candidate = value.strip().strip('"').strip("'")
    if len(candidate) < 5:
        return False
    if _looks_like_path_noise(candidate):
        return False
    lowered = candidate.lower()
    if " " in candidate and not re.search(r"\.(py|md|toml|json|yaml|yml|js|ts|dart|txt)\b", lowered):
        return False
    return bool(
        re.search(r"\.(py|md|toml|json|yaml|yml|js|ts|dart|txt|lock)\b", lowered)
        or candidate.startswith(("src/", "tests/", "docs/", "agent-memory-orchestrator/"))
        or re.match(r"^[A-Za-z]:\\", candidate)
    )


def _looks_like_path_noise(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(("single literal token", "list of allowed", "wall time:", "output:"))


def _looks_like_test_or_lint(command: str) -> bool:
    return any(token in command for token in ("pytest", "ruff", "npm test", "flutter analyze", "dart test", "cargo test"))


def _looks_like_read_or_search(command: str) -> bool:
    return any(
        token in command
        for token in (
            "rg ",
            "select-string",
            "get-content",
            "cat ",
            "type ",
            "git grep",
            "get-childitem",
            "ls ",
            "dir ",
        )
    )


def _looks_like_filesystem_write(command: str) -> bool:
    return any(
        token in command
        for token in (
            "new-item",
            "mkdir",
            "set-content",
            "out-file",
            "copy-item",
            "move-item",
        )
    )


def _looks_like_environment_check(command: str) -> bool:
    return any(token in command for token in ("get-process", "test-path", "where.exe", "get-command", "--version"))


def _contains_command(command: str, value: str) -> bool:
    return value in command.replace("\\", "/")


def _git_subcommand(command: str) -> str:
    tokens = command.replace("\\", "/").split()
    try:
        index = tokens.index("git")
    except ValueError:
        return ""
    index += 1
    while index < len(tokens):
        token = tokens[index]
        if token.lower() == "-c":
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return ""


def _one_line(value: str, limit: int) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " | ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = str(value or "").strip().strip('"').strip("'")
        if not clean:
            continue
        key = _norm(clean)
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return tuple(out)


def _norm(value: str) -> str:
    return value.replace("\\", "/").strip().lower()
