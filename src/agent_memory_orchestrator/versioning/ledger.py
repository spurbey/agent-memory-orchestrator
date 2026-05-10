from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .base import VersionBackend
from .models import GitCommitDetails, GitDiffSummary


@dataclass(slots=True, frozen=True)
class WorkTrace:
    commit: GitCommitDetails
    diff: GitDiffSummary
    patch_id: str
    test_evidence_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "commit": self.commit.as_dict(),
            "diff": self.diff.as_dict(),
            "patch_id": self.patch_id,
            "test_evidence_ids": self.test_evidence_ids,
        }


class WorkLedger:
    """Read-only Git work ledger helper.

    AMO anchors graph reasoning to Git commits and diffs without replacing Git.
    """

    def __init__(self, backend: VersionBackend) -> None:
        self.backend = backend

    def trace_commit(
        self,
        commit: str = "HEAD",
        *,
        cwd: str | None = None,
        test_evidence_ids: list[str] | None = None,
    ) -> WorkTrace:
        details = self.backend.commit_details(commit=commit, cwd=cwd)
        diff = self.backend.diff_summary(commit=commit, cwd=cwd)
        patch_id = self.backend.patch_id(commit=commit, cwd=cwd) or _fallback_patch_id(diff)
        return WorkTrace(
            commit=details,
            diff=diff,
            patch_id=patch_id,
            test_evidence_ids=test_evidence_ids or [],
        )


def _fallback_patch_id(diff: GitDiffSummary) -> str:
    digest = hashlib.sha256()
    digest.update("\n".join(diff.changed_files).encode("utf-8"))
    digest.update(str(diff.insertions).encode("ascii"))
    digest.update(str(diff.deletions).encode("ascii"))
    return digest.hexdigest()
