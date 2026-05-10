from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class GitSnapshot:
    available: bool
    repo_root: str = ""
    branch: str = ""
    head: str = ""
    status_porcelain: str = ""
    changed_files: list[str] = field(default_factory=list)
    staged_files: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def is_dirty(self) -> bool:
        return bool(self.status_porcelain.strip())

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "repo_root": self.repo_root,
            "branch": self.branch,
            "head": self.head,
            "status_porcelain": self.status_porcelain,
            "changed_files": self.changed_files,
            "staged_files": self.staged_files,
            "dirty": self.is_dirty,
            "error": self.error,
        }


@dataclass(slots=True, frozen=True)
class GitCommitDetails:
    available: bool
    commit: str = ""
    author: str = ""
    authored_at: str = ""
    subject: str = ""
    body: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "commit": self.commit,
            "author": self.author,
            "authored_at": self.authored_at,
            "subject": self.subject,
            "body": self.body,
            "error": self.error,
        }


@dataclass(slots=True, frozen=True)
class GitDiffSummary:
    available: bool
    base: str = ""
    target: str = ""
    changed_files: list[str] = field(default_factory=list)
    insertions: int = 0
    deletions: int = 0
    summary: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "available": self.available,
            "base": self.base,
            "target": self.target,
            "changed_files": self.changed_files,
            "insertions": self.insertions,
            "deletions": self.deletions,
            "summary": self.summary,
            "error": self.error,
        }
