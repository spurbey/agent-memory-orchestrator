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


class VersionBackend(Protocol):
    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        """Return repository state for the current work context."""

    def commit_details(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitCommitDetails:
        """Return commit metadata."""

    def diff_summary(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitDiffSummary:
        """Return changed files and diff stats for a commit."""

    def patch_id(self, commit: str = "HEAD", cwd: str | Path | None = None) -> str:
        """Return Git patch-id for a commit when available."""


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

    def commit_details(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitCommitDetails:
        workdir = Path(cwd or ".").expanduser().resolve()
        try:
            root = Path(_git(["rev-parse", "--show-toplevel"], workdir))
            fmt = "%H%x1f%an <%ae>%x1f%aI%x1f%s%x1f%b"
            raw = _git(["show", "-s", f"--format={fmt}", commit], root)
            parts = raw.split("\x1f", 4)
            while len(parts) < 5:
                parts.append("")
            return GitCommitDetails(
                available=True,
                commit=parts[0],
                author=parts[1],
                authored_at=parts[2],
                subject=parts[3],
                body=parts[4],
            )
        except Exception as exc:
            return GitCommitDetails(available=False, commit=commit, error=str(exc))

    def diff_summary(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitDiffSummary:
        workdir = Path(cwd or ".").expanduser().resolve()
        try:
            root = Path(_git(["rev-parse", "--show-toplevel"], workdir))
            target = _git(["rev-parse", commit], root)
            base = _git(["rev-parse", f"{commit}^"], root, check=False)
            files = _split_lines(_git(["diff-tree", "--root", "--no-commit-id", "--name-only", "-r", target], root, check=False))
            stat = _git(["show", "--numstat", "--format=", "--no-renames", target], root, check=False)
            insertions = 0
            deletions = 0
            for line in stat.splitlines():
                cols = line.split("\t")
                if len(cols) >= 3:
                    insertions += _safe_int(cols[0])
                    deletions += _safe_int(cols[1])
            return GitDiffSummary(
                available=True,
                base=base,
                target=target,
                changed_files=files,
                insertions=insertions,
                deletions=deletions,
                summary=f"{len(files)} files changed, +{insertions}/-{deletions}",
            )
        except Exception as exc:
            return GitDiffSummary(available=False, target=commit, error=str(exc))

    def patch_id(self, commit: str = "HEAD", cwd: str | Path | None = None) -> str:
        workdir = Path(cwd or ".").expanduser().resolve()
        try:
            root = Path(_git(["rev-parse", "--show-toplevel"], workdir))
            diff = subprocess.run(
                ["git", "show", "--format=", commit],
                cwd=str(root),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if diff.returncode != 0:
                return ""
            patch = subprocess.run(
                ["git", "patch-id", "--stable"],
                cwd=str(root),
                input=diff.stdout,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if patch.returncode != 0:
                return ""
            return patch.stdout.split()[0] if patch.stdout.split() else ""
        except Exception:
            return ""


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


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        return 0
