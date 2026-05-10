from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .models import GitCommitDetails, GitDiffSummary, GitSnapshot


class VersionBackend(Protocol):
    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        """Return repository state for the current work context."""

    def commit_details(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitCommitDetails:
        """Return commit metadata."""

    def diff_summary(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitDiffSummary:
        """Return changed files and diff stats for a commit."""

    def patch_id(self, commit: str = "HEAD", cwd: str | Path | None = None) -> str:
        """Return Git patch-id for a commit when available."""
