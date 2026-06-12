from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Iterable

from ....domain.semantic_harness import SourceFile
from ....domain.semantic_harness import normalize_file_path


DEFAULT_INCLUDE_SUFFIXES = frozenset(
    {
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
)

DEFAULT_EXCLUDED_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "venv",
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        ".tmp",
        ".data",
        ".graph",
        ".evidence",
    }
)


@dataclass(slots=True, frozen=True)
class RepoBootstrapOptions:
    include_suffixes: frozenset[str] = DEFAULT_INCLUDE_SUFFIXES
    excluded_dirs: frozenset[str] = DEFAULT_EXCLUDED_DIRS
    max_file_bytes: int = 256_000
    max_files: int = 10_000
    prefer_git_tracked: bool = True


@dataclass(slots=True, frozen=True)
class RepoSourceSnapshot:
    repo_root: Path
    files: tuple[SourceFile, ...]
    skipped: tuple[dict[str, str], ...]


def read_repo_source_files(repo_root: str | Path, options: RepoBootstrapOptions | None = None) -> RepoSourceSnapshot:
    opts = options or RepoBootstrapOptions()
    root = Path(repo_root).resolve()
    files: list[SourceFile] = []
    skipped: list[dict[str, str]] = []
    if not root.exists() or not root.is_dir():
        raise ValueError(f"repo root does not exist or is not a directory: {root}")
    for path in _candidate_paths(root, opts):
        if len(files) >= opts.max_files:
            skipped.append({"path": "", "reason": "max_files_reached"})
            break
        if not path.is_file():
            continue
        rel = normalize_file_path(path.relative_to(root))
        if _is_excluded(rel, opts.excluded_dirs):
            continue
        suffix = path.suffix.lower()
        if suffix not in opts.include_suffixes:
            skipped.append({"path": rel, "reason": "unsupported_suffix"})
            continue
        try:
            size = path.stat().st_size
        except OSError:
            skipped.append({"path": rel, "reason": "stat_failed"})
            continue
        if size > opts.max_file_bytes:
            skipped.append({"path": rel, "reason": "file_too_large"})
            continue
        try:
            data = path.read_bytes()
        except OSError:
            skipped.append({"path": rel, "reason": "read_failed"})
            continue
        if b"\x00" in data:
            skipped.append({"path": rel, "reason": "binary_file"})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
            skipped.append({"path": rel, "reason": "decode_replacement"})
        files.append(SourceFile(path=rel, text=text, language=_language_for_suffix(suffix)))
    return RepoSourceSnapshot(repo_root=root, files=tuple(files), skipped=tuple(skipped))


def _candidate_paths(root: Path, opts: RepoBootstrapOptions) -> Iterable[Path]:
    if opts.prefer_git_tracked:
        tracked = _git_tracked_files(root)
        if tracked:
            return tracked
    return sorted(root.rglob("*"))


def _git_tracked_files(root: Path) -> tuple[Path, ...]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ()
    if result.returncode != 0 or not result.stdout:
        return ()
    paths: list[Path] = []
    for raw in result.stdout.split(b"\x00"):
        if not raw:
            continue
        try:
            rel = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        paths.append(root / rel)
    return tuple(sorted(paths))


def _is_excluded(rel_path: str, excluded_dirs: frozenset[str]) -> bool:
    parts = set(rel_path.split("/"))
    return any(part in excluded_dirs for part in parts)


def _language_for_suffix(suffix: str) -> str:
    return {
        ".py": "python",
        ".md": "markdown",
        ".json": "json",
        ".toml": "toml",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix, suffix.lstrip("."))


__all__ = [
    "DEFAULT_EXCLUDED_DIRS",
    "DEFAULT_INCLUDE_SUFFIXES",
    "RepoBootstrapOptions",
    "RepoSourceSnapshot",
    "read_repo_source_files",
]
