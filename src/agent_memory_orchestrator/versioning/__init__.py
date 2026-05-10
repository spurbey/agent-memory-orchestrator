"""Git-backed versioning and work-ledger primitives."""

from .base import VersionBackend
from .git import LocalGitBackend
from .ledger import WorkLedger, WorkTrace
from .models import GitCommitDetails, GitDiffSummary, GitSnapshot

__all__ = [
    "GitCommitDetails",
    "GitDiffSummary",
    "GitSnapshot",
    "LocalGitBackend",
    "VersionBackend",
    "WorkLedger",
    "WorkTrace",
]
