from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


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


class VersionBackend(Protocol):
    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        """Return repository state for the current work context."""


class LocalGitBackend:
    """Local Git implementation for the version graph.

    This is intentionally read-only. Commit creation remains the user's/agent's
    job; AMO only observes commits and links graph nodes to them.
    """

    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        workdir = Path(cwd or ".").expanduser().resolve()
        try:
            repo_root = _git(["rev-parse", "--show-toplevel"], workdir)
            root = Path(repo_root)
            branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
            head = _git(["rev-parse", "HEAD"], root)
            status = _git(["status", "--porcelain"], root, check=False)
            changed = _split_lines(_git(["diff", "--name-only"], root, check=False))
            staged = _split_lines(_git(["diff", "--cached", "--name-only"], root, check=False))
            return GitSnapshot(
                available=True,
                repo_root=str(root),
                branch=branch,
                head=head,
                status_porcelain=status,
                changed_files=changed,
                staged_files=staged,
            )
        except Exception as exc:
            return GitSnapshot(available=False, error=str(exc))


def _git(args: list[str], cwd: Path, *, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]

