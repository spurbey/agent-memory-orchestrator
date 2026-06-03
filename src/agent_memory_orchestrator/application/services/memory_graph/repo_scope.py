from __future__ import annotations

from pathlib import Path
from typing import Any

from ....domain.versioning.repo_identity import resolve_repo_identity


def repo_id_for_path(path: str | Path, cache: dict[str, str]) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    if text not in cache:
        cache[text] = resolve_repo_identity(text).repo_id
    return cache[text]


def node_repo_id(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("repo_id") or metadata.get("repo_id") or "")


def node_repo_path(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("repo_path") or metadata.get("repo_path") or metadata.get("repo_root") or "")


def matches_repo_scope(node: dict[str, Any], repo_id: str) -> bool:
    if not repo_id:
        return True
    return node_repo_id(node) == repo_id


__all__ = ["matches_repo_scope", "node_repo_id", "node_repo_path", "repo_id_for_path"]
