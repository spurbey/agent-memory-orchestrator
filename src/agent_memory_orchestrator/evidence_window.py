from __future__ import annotations

import json
import re
from typing import Any

from .graph_triggers import TriggerDecision


MAX_QWEN_RECORDS = 8
MAX_QWEN_CONTENT_CHARS = 480
MAX_QWEN_TOTAL_CHARS = 2600

_WRITE_TOOLS = {"apply_patch", "edit", "write", "shell_command"}
_READ_ONLY_TOOLS = {"rg", "grep", "cat", "get-content", "select-string", "ls", "dir"}
_FORBIDDEN_TEXT = (
    "captureonly",
    "manualsmoke",
    "hook_event_name",
    "status_porcelain",
    "after_preview",
    "from __future__",
    "class ",
    "def ",
)


def clean_evidence_window(
    records: list[dict[str, Any]],
    trigger: TriggerDecision,
    *,
    max_records: int = MAX_QWEN_RECORDS,
    max_content_chars: int = MAX_QWEN_CONTENT_CHARS,
    max_total_chars: int = MAX_QWEN_TOTAL_CHARS,
) -> list[dict[str, Any]]:
    """Return bounded, structured evidence for Qwen graph extraction.

    Raw evidence remains append-only on disk. This function only controls what
    the local LLM sees when creating session graph nodes.
    """

    selected = _select_records(records, trigger, max_records=max_records)
    cleaned: list[dict[str, Any]] = []
    for index, record in enumerate(selected, start=1):
        row = _clean_record(record, trigger, index=index, max_content_chars=max_content_chars)
        if row:
            cleaned.append(row)
    return _fit_total_budget(cleaned, max_total_chars=max_total_chars, max_content_chars=max_content_chars)


def _select_records(records: list[dict[str, Any]], trigger: TriggerDecision, *, max_records: int) -> list[dict[str, Any]]:
    if not records:
        return []
    chosen: list[dict[str, Any]] = []
    trigger_record = records[-1]

    last_prompt = next((record for record in reversed(records[:-1]) if _prompt_text(record)), None)
    if last_prompt is not None:
        chosen.append(last_prompt)

    budget = max(1, max_records - 1)
    recent_relevant: list[dict[str, Any]] = []
    for record in reversed(records[:-1]):
        if record is last_prompt:
            continue
        if _is_relevant_record(record, trigger):
            recent_relevant.append(record)
        if len(recent_relevant) >= budget:
            break

    chosen.extend(reversed(recent_relevant))
    chosen.append(trigger_record)
    return _unique_records(chosen)[-max_records:]


def _clean_record(
    record: dict[str, Any],
    trigger: TriggerDecision,
    *,
    index: int,
    max_content_chars: int,
) -> dict[str, Any] | None:
    payload = _payload(record)
    event_name = _event_name(record)
    if _is_hook_capture_response(payload):
        return None

    if prompt := _prompt_text(record):
        return _compact_row(index, event_name, "user_goal", _safe_summary(prompt, max_content_chars))

    if event_name in {"stop", "session_stop"}:
        if trigger.trigger_type != "stop_finalize":
            return None
        message = str(payload.get("last_assistant_message") or "").strip()
        return _compact_row(index, event_name, "finalize", _safe_summary(message, max_content_chars))

    tool = str(payload.get("tool") or payload.get("tool_name") or "").strip()
    command = _tool_command(payload)
    raw = str(payload.get("tool_response") or payload.get("content") or payload.get("message") or "")
    raw_summary = _strip_jsonish_noise(raw) or raw
    lowered = f"{tool} {command} {raw_summary}".lower()

    if _looks_like_test(lowered):
        tests = _test_summaries(raw_summary or command)
        summary = "; ".join(tests) or _safe_summary(command or raw, max_content_chars)
        return _compact_row(
            index,
            event_name,
            "test_run",
            summary,
            tool=tool,
            command=_safe_command(command),
            tests=tests,
        )

    if _looks_like_git(lowered):
        commit_ids = _extract_commits(raw_summary + "\n" + command)
        return _compact_row(
            index,
            event_name,
            "git",
            _git_summary(command, raw_summary, max_content_chars=max_content_chars),
            tool=tool,
            command=_safe_command(command),
            commits=commit_ids,
            changed_files=_extract_files(raw_summary),
        )

    if _looks_like_write(tool, command, raw_summary):
        files = _extract_files(raw_summary + "\n" + command)
        summary = _write_summary(tool, command, raw_summary, files, max_content_chars=max_content_chars)
        return _compact_row(
            index,
            event_name,
            "code_write",
            summary,
            tool=tool,
            command=_safe_command(command),
            changed_files=files,
        )

    if _contains_durable_statement(raw_summary):
        return _compact_row(index, event_name, "work_note", _safe_summary(raw_summary, max_content_chars))

    return None


def _compact_row(
    index: int,
    event_name: str,
    kind: str,
    summary: str,
    **extra: Any,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "evidence_index": index,
        "event_name": event_name,
        "kind": kind,
        "summary": summary,
    }
    for key, value in extra.items():
        if value:
            row[key] = value
    return row


def _fit_total_budget(
    rows: list[dict[str, Any]],
    *,
    max_total_chars: int,
    max_content_chars: int,
) -> list[dict[str, Any]]:
    fitted: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        candidate = dict(row)
        encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
        if used + len(encoded) > max_total_chars:
            remaining = max_total_chars - used - 120
            if remaining <= 80:
                break
            candidate["summary"] = _trim(str(candidate.get("summary") or ""), min(max_content_chars, remaining))
            encoded = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
            if used + len(encoded) > max_total_chars:
                break
        fitted.append(candidate)
        used += len(encoded)
    return fitted


def _is_relevant_record(record: dict[str, Any], trigger: TriggerDecision) -> bool:
    payload = _payload(record)
    if _is_hook_capture_response(payload):
        return False
    if _prompt_text(record):
        return True
    text = _record_text(record)
    if trigger.is_write and _looks_like_write(str(payload.get("tool") or ""), _tool_command(payload), text):
        return True
    if trigger.is_test and _looks_like_test(text):
        return True
    if trigger.is_git and _looks_like_git(text):
        return True
    if trigger.trigger_type in {"explicit_finalize", "stop_finalize"} and _contains_durable_statement(text):
        return True
    return _contains_durable_statement(text)


def _is_hook_capture_response(payload: dict[str, Any]) -> bool:
    return payload.get("continue") is True and (
        payload.get("captureOnly") is True or payload.get("manualSmoke") is not None
    )


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else {}


def _event_name(record: dict[str, Any]) -> str:
    payload = _payload(record)
    raw = str(record.get("event_name") or payload.get("hook_event_name") or "message")
    return _snake(raw)


def _prompt_text(record: dict[str, Any]) -> str:
    payload = _payload(record)
    value = payload.get("prompt")
    return str(value).strip() if isinstance(value, str) and value.strip() else ""


def _tool_command(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        return str(tool_input.get("command") or tool_input.get("cmd") or "").strip()
    if isinstance(tool_input, str):
        return tool_input.strip()
    return ""


def _record_text(record: dict[str, Any]) -> str:
    payload = _payload(record)
    chunks = [
        str(record.get("event_name") or ""),
        str(payload.get("tool") or ""),
        str(payload.get("tool_name") or ""),
        str(payload.get("content") or ""),
        str(payload.get("message") or ""),
        str(payload.get("tool_response") or ""),
        _tool_command(payload),
    ]
    return "\n".join(chunks).lower()


def _looks_like_write(tool: str, command: str, raw: str) -> bool:
    lowered = f"{tool} {command} {raw}".lower()
    if "apply_patch" in lowered or "updated the following files" in lowered:
        return True
    if "success. updated the following files" in lowered:
        return True
    write_terms = ("set-content", "add-content", "out-file", "write_text", "write_bytes")
    return any(term in lowered for term in write_terms)


def _looks_like_test(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("pytest", "ruff check", "passed", "failed", "all checks passed"))


def _looks_like_git(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("git status", "git diff", "git add", "git commit", "git show", "git log"))


def _contains_durable_statement(text: str) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in ("decision", "decided", "fix", "fixed", "bug", "blocker", "next step"))


def _write_summary(tool: str, command: str, raw: str, files: list[str], *, max_content_chars: int) -> str:
    durable = _safe_summary(raw, max_content_chars)
    if files:
        summary = f"Code edit applied to: {', '.join(files[:8])}"
        if _contains_durable_statement(durable):
            summary = f"{summary}. {durable}"
        return _trim(summary, max_content_chars)
    command_text = _safe_command(command)
    if command_text:
        return _trim(f"Code edit command executed: {command_text}", max_content_chars)
    tool_text = tool or "write tool"
    return _trim(f"{tool_text} applied a code edit.", max_content_chars)


def _git_summary(command: str, raw: str, *, max_content_chars: int) -> str:
    commits = _extract_commits(raw + "\n" + command)
    if commits:
        return _trim(f"Git operation referenced commit {commits[0]}.", max_content_chars)
    if "git commit" in command.lower() or "git commit" in raw.lower():
        return "Git commit operation executed."
    if command:
        return _trim(f"Git command executed: {_safe_command(command)}", max_content_chars)
    return "Git operation detected."


def _test_summaries(raw: str) -> list[str]:
    lines = [" ".join(line.strip().split()) for line in raw.splitlines() if line.strip()]
    selected = [
        _trim(line, 180)
        for line in lines
        if any(term in line.lower() for term in ("passed", "failed", "error", "all checks passed"))
    ]
    return selected[:5]


def _safe_command(command: str) -> str:
    command = _strip_forbidden(_trim(command, 240))
    lowered = command.lower()
    if any(lowered.startswith(tool) for tool in _READ_ONLY_TOOLS):
        return ""
    return command


def _safe_summary(text: str, max_content_chars: int) -> str:
    text = _strip_jsonish_noise(text)
    text = _strip_code_blob(text)
    text = _strip_forbidden(text)
    return _trim(text, max_content_chars)


def _strip_jsonish_noise(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped:
        return ""
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            return stripped
        return _summarize_json(parsed)
    return stripped


def _summarize_json(value: Any) -> str:
    if isinstance(value, dict):
        output = str(value.get("output") or value.get("message") or value.get("summary") or "")
        if output:
            return output
        metadata = value.get("metadata")
        if isinstance(metadata, dict) and metadata.get("exit_code") is not None:
            return f"Command exited with code {metadata.get('exit_code')}."
    return ""


def _strip_code_blob(text: str) -> str:
    lines = [" ".join(line.strip().split()) for line in str(text or "").splitlines() if line.strip()]
    safe: list[str] = []
    for line in lines:
        lowered = line.lower()
        if lowered.startswith(("from __future__", "import ", "class ", "def ", "@dataclass")):
            continue
        if re.match(r"^(return|if|for|while|try|except|with)\b", lowered):
            continue
        safe.append(line)
    return " | ".join(safe)


def _strip_forbidden(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(r"raw_[0-9a-fA-F]{8,}", "evidence_ref", cleaned)
    cleaned = re.sub(r'"?(hook_event_name|captureOnly|manualSmoke|status_porcelain|after_preview)"?\s*[:=]\s*[^,}\n]+', "", cleaned)
    for term in _FORBIDDEN_TEXT:
        cleaned = re.sub(re.escape(term), "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split())


def _extract_files(text: str) -> list[str]:
    normalized = str(text or "").replace("\\\\", "/").replace("\\", "/")
    tokens = re.split(r"[\s\"'`|,{}[\]():]+", normalized)
    seen: list[str] = []
    for token in tokens:
        clean = token.strip().strip(";")
        clean = re.sub(r"^(M|A|D|R)\s+", "", clean)
        clean = re.sub(r"^[A-Z]:/", "", clean, flags=re.IGNORECASE)
        if "/src/" in clean:
            clean = clean[clean.index("/src/") + 1 :]
        clean = clean.lstrip("./")
        if not re.search(r"(?i)\.(py|js|ts|tsx|jsx|md|toml|json|yaml|yml|css|html|rs|go)$", clean):
            continue
        if clean and clean not in seen:
            seen.append(clean)
    return seen[:20]


def _extract_commits(text: str) -> list[str]:
    commits = re.findall(r"\b[0-9a-f]{7,40}\b", text, flags=re.IGNORECASE)
    seen: list[str] = []
    for commit in commits:
        lowered = commit.lower()
        if lowered not in seen:
            seen.append(lowered)
    return seen[:5]


def _unique_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[int] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        marker = id(record)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(record)
    return unique


def _trim(value: str, limit: int) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"
