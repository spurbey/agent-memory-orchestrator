from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REASONING_EVIDENCE_VIEW_STAGE = "02b_reasoning_evidence_view_tight"
REASONING_EVIDENCE_VIEW_SCHEMA_VERSION = "reasoning-evidence-view-v1"
DEFAULT_CODE_WRITE_SAMPLE_LIMIT = 200

_REASON_PATTERNS = re.compile(
    r"\b("
    r"i'll|i will|i'm|i found|root cause|because|should|needs|"
    r"correct|what changed|implemented|fixed|the issue|plan|decision|"
    r"architecture|not do|must|added|changed|validated|passed|failed|next"
    r")\b",
    re.IGNORECASE,
)
_NOISE_ASSISTANT_RE = re.compile(r"^(done\.?|ok\.?|yes\.?|no\.?)$", re.IGNORECASE)
_RAW_INTERNAL_ID_RE = re.compile(
    r"(?:transcript:[0-9a-f]{8,}-[0-9a-f-]+:[^\s\"']+|tool_(?:use|result):call_[A-Za-z0-9]+|call_[A-Za-z0-9]{10,})"
)


@dataclass(slots=True, frozen=True)
class ReasoningEvidenceViewBuild:
    view: dict[str, Any]
    support_ref_map: tuple[dict[str, Any], ...]
    code_write_support_sample: tuple[dict[str, Any], ...]
    quality: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": REASONING_EVIDENCE_VIEW_STAGE,
            "schema_version": REASONING_EVIDENCE_VIEW_SCHEMA_VERSION,
            "quality": self.quality,
            "view": self.view,
            "support_ref_map": list(self.support_ref_map),
            "code_write_support_sample": list(self.code_write_support_sample),
        }


def build_reasoning_evidence_view(
    raw_jsonl_path: Path,
    *,
    transcript_path: Path | None = None,
    repo_root: Path | None = None,
    stage: str = REASONING_EVIDENCE_VIEW_STAGE,
    support_ref_map_path: str = "support_ref_map.json",
    code_write_sample_limit: int = DEFAULT_CODE_WRITE_SAMPLE_LIMIT,
    max_transcript_line: int | None = None,
) -> ReasoningEvidenceViewBuild:
    """Build a compact reasoning evidence view from full raw evidence and transcript.

    This is the production form of the Stage 2B reset algorithm. It keeps raw
    transcript/tool ids in the support map only, while exposing short evidence
    refs for problem, assistant reasoning, commit, and validation facts.
    """

    raw_jsonl_path = raw_jsonl_path.resolve()
    repo_root = (repo_root or Path.cwd()).resolve()
    raw_records = tuple(load_jsonl(raw_jsonl_path))
    resolved_transcript = _resolve_transcript_path(raw_records, transcript_path=transcript_path)

    support: list[dict[str, Any]] = []
    problems: list[dict[str, Any]] = []
    reasoning: list[dict[str, Any]] = []
    commits: list[dict[str, Any]] = []
    validations: list[dict[str, Any]] = []
    code_write_support: list[dict[str, Any]] = []
    ref_counter = 0

    def add_ref(kind: str, line_no: int, timestamp: str, summary: str, extra: dict[str, Any] | None = None) -> str:
        nonlocal ref_counter
        ref_counter += 1
        row = {
            "ref": f"E{ref_counter:05d}",
            "kind": kind,
            "origin": "transcript",
            "line_no": line_no,
            "timestamp": timestamp,
            "summary": compact(summary, 300),
        }
        if extra:
            row.update(extra)
        support.append(row)
        return str(row["ref"])

    pending: dict[str, dict[str, Any]] = {}
    event_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    for line_no, item in load_jsonl(resolved_transcript):
        if max_transcript_line is not None and line_no > max_transcript_line:
            break
        raw_type = str(item.get("type") or "")
        pl = payload(item)
        payload_type = str(pl.get("type") or "")
        timestamp = str(item.get("timestamp") or "")
        event_counts[f"{raw_type}:{payload_type}"] += 1

        if raw_type == "response_item" and payload_type == "message":
            role = str(pl.get("role") or "")
            role_counts[role] += 1
            text = text_from_content(pl.get("content"))
            if role == "user":
                cleaned = clean_user_request(text)
                if keep_user_request(cleaned):
                    ref = add_ref("user_problem", line_no, timestamp, cleaned, {"role": role})
                    problems.append({"ref": ref, "timestamp": timestamp, "request": sanitize_main_view_text(cleaned)})
            elif role == "assistant":
                cleaned = compact(text, 1400)
                if keep_assistant_reasoning(cleaned):
                    ref = add_ref("assistant_reasoning", line_no, timestamp, cleaned, {"role": role})
                    reasoning.append({"ref": ref, "timestamp": timestamp, "statement": sanitize_main_view_text(cleaned)})
            continue

        if raw_type == "response_item" and payload_type in {"function_call", "custom_tool_call"}:
            call_id = str(pl.get("call_id") or "")
            _, tool_name, command = parse_tool_payload(pl)
            kind = classify_tool(tool_name, command)
            pending[call_id] = {
                "line_no": line_no,
                "timestamp": timestamp,
                "tool_name": tool_name,
                "command": command,
                "kind": kind,
                "call_id": call_id,
            }
            if kind in {"code_write", "validation", "git_context", "git_commit"}:
                ref = add_ref(kind, line_no, timestamp, command, {"tool_name": tool_name, "call_id": call_id})
                if kind == "code_write":
                    code_write_support.append({"ref": ref, "timestamp": timestamp, "command_preview": compact(command, 300)})
            continue

        if raw_type == "response_item" and payload_type in {"function_call_output", "custom_tool_call_output"}:
            call_id = str(pl.get("call_id") or "")
            output = text_from_content(pl.get("output") or pl.get("content"))
            prior = pending.get(call_id, {})
            tool_name = str(prior.get("tool_name") or "")
            command = str(prior.get("command") or "")
            kind = classify_tool(tool_name, command, output)
            if kind == "validation":
                ref = add_ref("validation_result", line_no, timestamp, output, {"call_id": call_id})
                validations.append(
                    {
                        "ref": ref,
                        "timestamp": timestamp,
                        "status": validation_result_status(output),
                        "command": sanitize_main_view_text(compact(command, 500)),
                        "output_preview": sanitize_main_view_text(compact(output, 700)),
                    }
                )
            if kind == "git_commit":
                commit_id, message = extract_commit_from_output(output)
                if commit_id:
                    ref = add_ref("git_commit", line_no, timestamp, f"{commit_id} {message}", {"commit_id": commit_id, "call_id": call_id})
                    commits.append(
                        {
                            "ref": ref,
                            "timestamp": timestamp,
                            "commit_id": commit_id,
                            "message_from_output": message,
                            "git_truth": git_commit_truth(commit_id, repo_root=repo_root),
                        }
                    )

    deduped_commits = _dedupe_commits(commits)
    view = {
        "stage": stage,
        "schema_version": REASONING_EVIDENCE_VIEW_SCHEMA_VERSION,
        "input_raw": str(raw_jsonl_path),
        "transcript_path": str(resolved_transcript),
        "raw_record_count": len(raw_records),
        "transcript_event_counts": dict(event_counts.most_common()),
        "role_counts": dict(role_counts),
        "user_problems": problems,
        "assistant_reasoning": reasoning,
        "commit_facts": deduped_commits,
        "validation_facts": validations,
        "code_write_support_count": len(code_write_support),
        "support_ref_map_path": support_ref_map_path,
        "policy": "Only problem/reasoning/commit/validation are candidate truth. Code writes and raw tool calls are support only.",
    }
    quality = {
        "raw_record_count": len(raw_records),
        "max_transcript_line": max_transcript_line,
        "user_problem_count": len(problems),
        "assistant_reasoning_count": len(reasoning),
        "commit_fact_count": len(deduped_commits),
        "validation_fact_count": len(validations),
        "code_write_support_count": len(code_write_support),
        "support_ref_count": len(support),
        "main_view_has_raw_internal_ids": reasoning_evidence_view_contains_raw_internal_ids(view),
        "acceptance": "PASS" if deduped_commits and not reasoning_evidence_view_contains_raw_internal_ids(view) else "FAIL",
    }
    return ReasoningEvidenceViewBuild(
        view=view,
        support_ref_map=tuple(support),
        code_write_support_sample=tuple(code_write_support[:code_write_sample_limit]),
        quality=quality,
    )


def write_reasoning_evidence_view_artifacts(build: ReasoningEvidenceViewBuild, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "reasoning_evidence_view.json").write_text(json.dumps(build.view, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "support_ref_map.json").write_text(json.dumps(list(build.support_ref_map), indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "code_write_support_sample.json").write_text(
        json.dumps(list(build.code_write_support_sample), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (output_dir / "stage2b_inventory.json").write_text(json.dumps(build.quality, indent=2, ensure_ascii=False), encoding="utf-8")
    (output_dir / "reasoning_evidence_view.md").write_text(_evidence_view_markdown(build), encoding="utf-8")
    (output_dir / "stage2b_review.md").write_text(_stage2b_review(build), encoding="utf-8")


def load_jsonl(path: Path) -> tuple[tuple[int, dict[str, Any]], ...]:
    rows: list[tuple[int, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                item = {"_parse_error": type(exc).__name__, "_raw": line[:500]}
            if isinstance(item, dict):
                rows.append((line_no, item))
    return tuple(rows)


def payload(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("payload") if isinstance(item.get("payload"), dict) else {}


def text_from_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str):
                    parts.append(text)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    if isinstance(value, dict):
        return text_from_content(value.get("content") or value.get("text") or value.get("output"))
    return ""


def compact(text: str, limit: int = 900) -> str:
    text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit] + (" ... <clipped>" if len(text) > limit else "")


def sanitize_main_view_text(text: str) -> str:
    return _RAW_INTERNAL_ID_RE.sub("[internal-id]", text)


def clean_user_request(text: str) -> str:
    marker = "## My request for Codex:"
    if marker in text:
        text = text.split(marker, 1)[1]
    text = re.sub(r"# Context from my IDE setup:.*?(?=## My request for Codex:|\Z)", "", text, flags=re.S)
    text = text.replace("<environment_context>", "").replace("</environment_context>", "")
    return compact(text, 1200)


def keep_user_request(text: str) -> bool:
    lowered = text.strip().lower()
    if len(lowered) < 8:
        return False
    if "turn_aborted" in lowered:
        return False
    if "<cwd>" in lowered and "<current_date>" in lowered:
        return False
    return not (lowered.startswith("ps ") and "my request" not in lowered and len(lowered) > 500)


def keep_assistant_reasoning(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 20:
        return False
    if _NOISE_ASSISTANT_RE.match(stripped):
        return False
    return bool(_REASON_PATTERNS.search(stripped))


def parse_tool_payload(pl: dict[str, Any]) -> tuple[str, str, str]:
    payload_type = str(pl.get("type") or "")
    name = str(pl.get("name") or pl.get("tool_name") or "")
    raw = pl.get("arguments") or pl.get("input") or pl.get("content") or ""
    if isinstance(raw, dict):
        command = str(raw.get("command") or raw.get("cmd") or json.dumps(raw, ensure_ascii=False))
    elif isinstance(raw, str):
        command = raw
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            command = str(parsed.get("command") or parsed.get("cmd") or raw)
    else:
        command = str(raw)
    if payload_type == "custom_tool_call" and not name:
        name = "custom_tool_call"
    return payload_type, name, command


def classify_tool(name: str, command: str, output: str = "") -> str:
    command_l = f"{name}\n{command}".lower()
    output_l = output.lower()
    if "apply_patch" in command_l or "*** begin patch" in command_l or "success. updated the following files" in output_l:
        return "code_write"
    if "git commit" in command_l or re.search(r"\[[^\]]+ [0-9a-f]{7,40}\]", output):
        return "git_commit"
    if any(marker in command_l for marker in ("git show", "git diff", "git log", "git status")):
        return "git_context"
    if re.search(r"\b(pytest|ruff|npm pack|npm test|mypy|python -m pytest|python -m ruff)\b", command_l):
        return "validation"
    if re.search(r"\b(get-content|rg |select-string|ls |dir |get-childitem|cat )", command_l):
        return "read_search"
    return "support_tool"


def validation_result_status(output: str) -> str:
    lowered = output.lower()
    if re.search(r"(all checks passed|\bpassed\b|exit code:\s*0|100% done)", lowered):
        return "pass"
    if re.search(r"(\bfailed\b|\berror\b|exit code:\s*[1-9])", lowered):
        return "fail"
    return "unknown"


def extract_commit_from_output(output: str) -> tuple[str, str]:
    match = re.search(r"\[[^\]]+\s+([0-9a-f]{7,40})\]\s+(.+)", output)
    return (match.group(1), match.group(2).strip()) if match else ("", "")


def git_commit_truth(commit: str, *, repo_root: Path) -> dict[str, Any]:
    verify = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    if verify.returncode != 0:
        return {"commit_id": commit, "resolved": False, "error": verify.stderr.strip()[:200]}
    full_sha = verify.stdout.strip()
    message = _git_output(["git", "show", "-s", "--format=%s", full_sha], repo_root=repo_root).strip()
    name_status_output = _git_output(["git", "show", "--name-status", "--no-renames", "--format=", full_sha], repo_root=repo_root)
    changed_files: list[str] = []
    name_status: list[dict[str, str]] = []
    for line in name_status_output.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            name_status.append({"status": parts[0], "path": parts[1]})
            changed_files.append(parts[1])
    parents = _git_output(["git", "show", "-s", "--format=%P", full_sha], repo_root=repo_root).strip().split()
    return {
        "commit_id": commit,
        "full_sha": full_sha,
        "resolved": True,
        "message": message,
        "parent_shas": parents,
        "changed_files": changed_files,
        "name_status": name_status,
    }


def reasoning_evidence_view_contains_raw_internal_ids(view: dict[str, Any]) -> bool:
    return bool(_RAW_INTERNAL_ID_RE.search(json.dumps(view, ensure_ascii=False)))


def _git_output(command: list[str], *, repo_root: Path) -> str:
    result = subprocess.run(command, cwd=repo_root, text=True, capture_output=True, check=False, encoding="utf-8", errors="replace")
    return result.stdout


def _resolve_transcript_path(
    raw_records: Iterable[tuple[int, dict[str, Any]]],
    *,
    transcript_path: Path | None,
) -> Path:
    if transcript_path is not None:
        return Path(_strip_windows_extended_path(str(transcript_path))).resolve()
    for _, item in raw_records:
        pl = payload(item)
        value = str(pl.get("transcript_path") or item.get("transcript_path") or "")
        if value:
            return Path(_strip_windows_extended_path(value)).resolve()
    raise ValueError("Could not resolve transcript_path from raw evidence")


def _strip_windows_extended_path(value: str) -> str:
    return value[4:] if value.startswith("\\\\?\\") else value


def _dedupe_commits(commits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for commit in commits:
        commit_id = str(commit.get("commit_id") or "")
        if commit_id and commit_id not in seen:
            seen.add(commit_id)
            out.append(commit)
    return out


def _evidence_view_markdown(build: ReasoningEvidenceViewBuild) -> str:
    view = build.view
    lines = [
        "# Stage 2B Tight Reasoning Evidence View",
        "",
        "This corrected view removes aborted turns, keeps patch mechanics support-only, and classifies validation only from actual validation commands.",
        "",
        "## Counts",
        f"- User problem candidates: `{len(view['user_problems'])}`",
        f"- Assistant reasoning candidates: `{len(view['assistant_reasoning'])}`",
        f"- Git commit facts: `{len(view['commit_facts'])}`",
        f"- Validation facts: `{len(view['validation_facts'])}`",
        f"- Code write support facts: `{view['code_write_support_count']}`",
        f"- Support refs: `{len(build.support_ref_map)}`",
        "",
        "## User Problem Sample",
    ]
    for problem in view["user_problems"][:30]:
        lines += [f"### {problem['ref']} {problem['timestamp']}", "```text", problem["request"], "```"]
    lines += ["", "## Assistant Reasoning Sample"]
    for item in view["assistant_reasoning"][:30]:
        lines += [f"### {item['ref']} {item['timestamp']}", "```text", item["statement"], "```"]
    lines += ["", "## Commit Facts"]
    for commit in view["commit_facts"][:80]:
        truth = commit.get("git_truth") if isinstance(commit.get("git_truth"), dict) else {}
        files = truth.get("changed_files") if isinstance(truth.get("changed_files"), list) else []
        file_text = ", ".join(str(path) for path in files[:8])
        if len(files) > 8:
            file_text += ", ..."
        lines += [
            f"### {commit['ref']} `{commit['commit_id']}`",
            f"- resolved: `{truth.get('resolved')}`",
            f"- git message: `{truth.get('message', '')}`",
            f"- changed files ({len(files)}): `{file_text}`",
        ]
    lines += ["", "## Validation Facts Sample"]
    for validation in view["validation_facts"][:40]:
        lines += [
            f"### {validation['ref']} `{validation['status']}`",
            f"- command: `{validation['command']}`",
            "```text",
            validation["output_preview"],
            "```",
        ]
    return "\n".join(lines)


def _stage2b_review(build: ReasoningEvidenceViewBuild) -> str:
    quality = build.quality
    return f"""# Stage 2B Review: Tight Reasoning Evidence View

## Why Stage 2A Was Not Accepted
- It kept aborted turns as user problems.
- It misclassified patch text containing test names as validation.
- It produced too much candidate noise for the intended reasoning graph.

## Correction
- Code writes are support-only before validation classification.
- Validation facts are created only from actual validation commands (`pytest`, `ruff`, `npm pack`, etc.).
- Aborted turns and environment-only user records are dropped from problem candidates.
- Internal transcript ids remain only in `support_ref_map.json`; the main view uses short refs.

## Counts
- User problem candidates: `{quality["user_problem_count"]}`
- Assistant reasoning candidates: `{quality["assistant_reasoning_count"]}`
- Git commit facts: `{quality["commit_fact_count"]}`
- Validation facts: `{quality["validation_fact_count"]}`
- Code write support facts: `{quality["code_write_support_count"]}`
- Support refs: `{quality["support_ref_count"]}`

## Acceptance For Stage 2
{"PASS" if quality["acceptance"] == "PASS" else "FAIL"}.

This is the right production shape: problem/reasoning/commit/validation as candidate truth, tool calls as support. It is intentionally broad because Stage 3 groups this evidence into commit/problem packets before any LLM extraction.

## Next Stage
Stage 3 builds `reasoning work packets`:
- one packet per Git commit or contiguous problem window
- include nearest user problem refs
- include nearest assistant rationale refs
- include Git truth and changed files
- include validation refs
- keep support refs short and resolve raw ids only through `support_ref_map.json`
"""
