from __future__ import annotations

import hashlib
from pathlib import Path


def normalize_file_path(path: str | Path) -> str:
    """Return a stable repo-relative path form for IDs and anchor matching."""

    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.strip("/")


def repo_id_for_root(root: str | Path) -> str:
    normalized = str(Path(root).resolve()).replace("\\", "/").lower()
    return f"repo:local:{_short_hash(normalized, size=16)}"


def file_id(repo_id: str, path: str | Path) -> str:
    return f"file:{_safe_repo(repo_id)}:{normalize_file_path(path)}"


def commit_id(repo_id: str, sha: str) -> str:
    normalized = str(sha or "").strip().lower()
    return f"commit:{_safe_repo(repo_id)}:{normalized or 'unknown'}"


def work_window_id(repo_id: str, session_id: str, sha: str) -> str:
    stable = "|".join([_safe_repo(repo_id), str(session_id or ""), str(sha or "")])
    return f"work_window:{_safe_repo(repo_id)}:{_short_hash(stable, size=20)}"


def hunk_id(repo_id: str, sha: str, path: str | Path, old_start: int, new_start: int) -> str:
    stable = "|".join([_safe_repo(repo_id), str(sha or ""), normalize_file_path(path), str(old_start), str(new_start)])
    return f"hunk:{_safe_repo(repo_id)}:{_short_hash(stable, size=24)}"


def symbol_id(repo_id: str, path: str | Path, qualified_name: str, symbol_kind: str) -> str:
    name = _normalize_name(qualified_name)
    kind = _normalize_name(symbol_kind)
    return f"symbol:{_safe_repo(repo_id)}:{normalize_file_path(path)}:{name}:{kind}"


def code_region_id(repo_id: str, path: str | Path, region_key: str, region_kind: str) -> str:
    key = _normalize_name(region_key)
    kind = _normalize_name(region_kind)
    return f"code_region:{_safe_repo(repo_id)}:{normalize_file_path(path)}:{kind}:{key}"


def doc_section_id(repo_id: str, path: str | Path, section_key: str, line_start: int) -> str:
    stable = "|".join([_safe_repo(repo_id), normalize_file_path(path), _normalize_name(section_key), str(line_start)])
    return f"doc_section:{_safe_repo(repo_id)}:{_short_hash(stable, size=24)}"


def docstring_id(repo_id: str, path: str | Path, target_key: str, doc_kind: str) -> str:
    stable = "|".join([_safe_repo(repo_id), normalize_file_path(path), _normalize_name(target_key), _normalize_name(doc_kind)])
    return f"docstring:{_safe_repo(repo_id)}:{_short_hash(stable, size=24)}"


def harness_card_id(repo_id: str, session_id: str, intent: str, support_node_ids: tuple[str, ...]) -> str:
    stable = "|".join([_safe_repo(repo_id), str(session_id or ""), str(intent or ""), *sorted(support_node_ids)])
    return f"hcard:{_safe_repo(repo_id)}:{_short_hash(stable, size=20)}"


def projection_doc_id(repo_id: str, source_node_id: str, doc_type: str) -> str:
    stable = "|".join([_safe_repo(repo_id), str(source_node_id or ""), _normalize_name(doc_type)])
    return f"pdoc:{_safe_repo(repo_id)}:{_short_hash(stable, size=24)}"


def version_id(entity_id: str, snapshot_id: str) -> str:
    stable = "|".join([str(entity_id or ""), str(snapshot_id or "")])
    return f"version:{_short_hash(stable, size=24)}"


def _safe_repo(repo_id: str) -> str:
    return str(repo_id or "").strip() or "repo:unknown"


def _normalize_name(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _short_hash(value: str, *, size: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


__all__ = [
    "code_region_id",
    "commit_id",
    "doc_section_id",
    "docstring_id",
    "file_id",
    "harness_card_id",
    "hunk_id",
    "normalize_file_path",
    "projection_doc_id",
    "repo_id_for_root",
    "symbol_id",
    "version_id",
    "work_window_id",
]
