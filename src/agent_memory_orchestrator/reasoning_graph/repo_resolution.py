from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_ABS_WINDOWS_PATH_RE = re.compile(r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\s]+\\)*[^\\/:*?\"<>|\s]*")
_ABS_POSIX_PATH_RE = re.compile(r"(?<![\w.-])/(?:[^\s\"'<>|]+/)*[^\s\"'<>|]+")
_COMMIT_OUTPUT_RE = re.compile(r"\[[^\]]+\s+([0-9a-f]{7,40})\]\s+.+")
_GIT_LOG_RE = re.compile(r"(?m)^([0-9a-f]{7,40})\s+.+$")


@dataclass(slots=True, frozen=True)
class SessionRepoResolution:
    repo_root: str
    source: str
    fallback_repo_path: str
    candidate_count: int
    commit_count: int
    resolved_commit_count: int
    candidates: tuple[dict[str, Any], ...]
    commit_ids_sample: tuple[str, ...]

    @property
    def resolved(self) -> bool:
        return bool(self.repo_root)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo_root": self.repo_root,
            "source": self.source,
            "fallback_repo_path": self.fallback_repo_path,
            "candidate_count": self.candidate_count,
            "commit_count": self.commit_count,
            "resolved_commit_count": self.resolved_commit_count,
            "candidates": list(self.candidates),
            "commit_ids_sample": list(self.commit_ids_sample),
        }


def resolve_session_repo_root(
    records: Iterable[dict[str, Any]],
    *,
    transcript_path: str | Path | None = None,
    fallback_repo_path: str | Path | None = None,
    max_candidates: int = 80,
    max_commits: int = 250,
) -> SessionRepoResolution:
    """Resolve the Git root that owns a closed V2 session.

    Hook cwd can be a parent workspace while tool calls operate inside a nested
    repository. We therefore score all candidate Git roots by actual commit
    resolution and prefer the root that owns the session's commit facts.
    """

    rows = tuple(records)
    fallback = str(fallback_repo_path or "").strip()
    transcript = _resolve_transcript_path(rows, transcript_path=transcript_path)
    paths = _candidate_paths(rows, transcript_path=transcript, fallback=fallback, max_candidates=max_candidates)
    roots = _candidate_git_roots(paths)
    commit_ids = _commit_ids_from_transcript(transcript, max_commits=max_commits) if transcript else ()

    scored: list[dict[str, Any]] = []
    for root in roots:
        resolved = _resolved_commit_count(root, commit_ids) if commit_ids else 0
        scored.append(
            {
                "repo_root": str(root),
                "resolved_commit_count": resolved,
                "path_depth": len(root.parts),
                "source": "candidate",
            }
        )

    scored.sort(key=lambda item: (-int(item["resolved_commit_count"]), -int(item["path_depth"]), str(item["repo_root"]).lower()))
    if scored:
        best = scored[0]
        return SessionRepoResolution(
            repo_root=str(best["repo_root"]),
            source="commit_resolution" if commit_ids and int(best["resolved_commit_count"]) > 0 else "git_root_candidate",
            fallback_repo_path=fallback,
            candidate_count=len(scored),
            commit_count=len(commit_ids),
            resolved_commit_count=int(best["resolved_commit_count"]),
            candidates=tuple(scored[:20]),
            commit_ids_sample=tuple(commit_ids[:20]),
        )

    return SessionRepoResolution(
        repo_root="",
        source="unresolved",
        fallback_repo_path=fallback,
        candidate_count=0,
        commit_count=len(commit_ids),
        resolved_commit_count=0,
        candidates=(),
        commit_ids_sample=tuple(commit_ids[:20]),
    )


def _resolve_transcript_path(records: tuple[dict[str, Any], ...], *, transcript_path: str | Path | None) -> Path | None:
    if transcript_path:
        path = Path(_strip_windows_extended_path(str(transcript_path))).expanduser()
        return path if path.exists() else None
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        for value in (payload.get("transcript_path"), record.get("transcript_path")):
            if isinstance(value, str) and value.strip():
                path = Path(_strip_windows_extended_path(value.strip())).expanduser()
                if path.exists():
                    return path
    return None


def _candidate_paths(
    records: tuple[dict[str, Any], ...],
    *,
    transcript_path: Path | None,
    fallback: str,
    max_candidates: int,
) -> tuple[str, ...]:
    out: list[str] = []
    if fallback:
        out.append(fallback)
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        _collect_paths(payload, out)
        _collect_paths(record, out)
    if transcript_path is not None:
        out.extend(_paths_from_transcript(transcript_path, max_items=max_candidates))
    return _dedupe(out)[:max_candidates]


def _collect_paths(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"cwd", "repo_path", "repo_root", "workspace", "workspace_root", "workdir"} and isinstance(item, str):
                out.append(item)
            elif key in {"tool_input", "input", "arguments"}:
                _collect_paths(item, out)
        return
    if isinstance(value, str):
        out.extend(_path_tokens(value))


def _paths_from_transcript(path: Path, *, max_items: int) -> tuple[str, ...]:
    out: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if len(out) >= max_items:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if payload.get("type") not in {"function_call", "custom_tool_call"}:
                continue
            args = _jsonish(payload.get("arguments") or payload.get("input") or {})
            if isinstance(args, dict):
                for key in ("workdir", "cwd", "repo_path", "repo_root"):
                    value = args.get(key)
                    if isinstance(value, str) and value.strip():
                        out.append(value.strip())
                command = args.get("command") or args.get("cmd")
                if isinstance(command, str):
                    out.extend(_path_tokens(command))
            elif isinstance(args, str):
                out.extend(_path_tokens(args))
    return tuple(_dedupe(out)[:max_items])


def _path_tokens(value: str) -> list[str]:
    tokens = _ABS_WINDOWS_PATH_RE.findall(value) + _ABS_POSIX_PATH_RE.findall(value)
    return [_clean_path_token(token) for token in tokens if _clean_path_token(token)]


def _clean_path_token(value: str) -> str:
    return value.strip().strip("`'\".,;:)]}")


def _candidate_git_roots(paths: tuple[str, ...]) -> tuple[Path, ...]:
    roots: list[str] = []
    for raw in paths:
        root = _git_root(raw)
        if root is not None:
            roots.append(str(root))
    return tuple(Path(item) for item in _dedupe(roots))


def _git_root(raw_path: str) -> Path | None:
    if not raw_path:
        return None
    path = Path(_strip_windows_extended_path(raw_path)).expanduser()
    if not path.exists():
        existing = next((parent for parent in (path, *path.parents) if parent.exists()), None)
        if existing is None:
            return None
        path = existing
    if path.is_file():
        path = path.parent
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=path,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    root = result.stdout.strip()
    return Path(root).resolve() if root else None


def _commit_ids_from_transcript(path: Path, *, max_commits: int) -> tuple[str, ...]:
    commits: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if len(commits) >= max_commits:
                break
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            if payload.get("type") not in {"function_call_output", "custom_tool_call_output"}:
                continue
            text = _text_from_value(payload.get("output") or payload.get("content"))
            for regex in (_COMMIT_OUTPUT_RE, _GIT_LOG_RE):
                for match in regex.finditer(text):
                    commits.append(match.group(1))
                    if len(commits) >= max_commits:
                        break
    return tuple(_dedupe(commits))


def _resolved_commit_count(repo_root: Path, commit_ids: tuple[str, ...]) -> int:
    count = 0
    for commit in commit_ids:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"],
            cwd=repo_root,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            count += 1
    return count


def _jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _text_from_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _text_from_value(value.get("content") or value.get("text") or value.get("output"))
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.append(_text_from_value(item))
        return "\n".join(part for part in parts if part)
    return ""


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        safe = str(value or "").strip()
        key = safe.lower()
        if safe and key not in seen:
            seen.add(key)
            out.append(safe)
    return out


def _strip_windows_extended_path(value: str) -> str:
    return value[4:] if value.startswith("\\\\?\\") else value
