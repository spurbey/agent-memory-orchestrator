"""Git infrastructure adapters."""

from __future__ import annotations

from .backend import GitCommitDetails
from .backend import GitDiffSummary
from .backend import GitSnapshot
from .backend import LocalGitBackend
from .backend import VersionBackend
from .diff import git_file_at_commit
from .diff import git_unified_zero_diff

__all__ = [
    "GitCommitDetails",
    "GitDiffSummary",
    "GitSnapshot",
    "LocalGitBackend",
    "VersionBackend",
    "git_file_at_commit",
    "git_unified_zero_diff",
]
