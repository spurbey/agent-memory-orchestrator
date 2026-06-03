from __future__ import annotations

from pathlib import Path
from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class GitPort(Protocol):
    """Git boundary for commit-backed reasoning workflows."""

    def root_for(self, path: Path) -> Path | None:
        """Resolve the Git root that owns a path."""

    def commit_metadata(self, repo: Path, commit_sha: str) -> Mapping[str, object]:
        """Return stable metadata for one commit."""
