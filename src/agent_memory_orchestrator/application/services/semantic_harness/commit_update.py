from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from ....domain.semantic_harness import CommitWorkWindow
from ....domain.semantic_harness import GraphUpdateDelta
from ....domain.semantic_harness import parse_unified_diff_hunks
from ....domain.semantic_harness import repo_id_for_root
from .repository import RepoBootstrapOptions
from .structural import StructuralHarnessService
from .structural import StructuralRepoBootstrapResult


@dataclass(slots=True, frozen=True)
class CommitUpdateBuildResult:
    repo_root: Path
    repo_id: str
    commit_sha: str
    commit_message: str
    diff_hunk_count: int
    bootstrap: StructuralRepoBootstrapResult
    work_window: CommitWorkWindow
    delta: GraphUpdateDelta


class CommitUpdateService:
    """Build deterministic Semantic Harness update deltas from Git commits."""

    def __init__(self, harness: StructuralHarnessService | None = None) -> None:
        self.harness = harness or StructuralHarnessService()

    def build_delta_for_commit(
        self,
        repo_root: str | Path,
        commit_sha: str,
        *,
        repo_id: str = "",
        session_id: str = "",
        options: RepoBootstrapOptions | None = None,
    ) -> CommitUpdateBuildResult:
        root = Path(repo_root).resolve()
        resolved_repo_id = repo_id or repo_id_for_root(root)
        full_sha, message = _read_commit_metadata(root, commit_sha)
        diff_text = _read_commit_diff(root, full_sha)
        hunks = parse_unified_diff_hunks(diff_text)
        bootstrap = self.harness.bootstrap_repo(root, repo_id=resolved_repo_id, options=options)
        work_window = CommitWorkWindow(
            repo_id=resolved_repo_id,
            session_id=session_id or f"git:{full_sha[:12]}",
            commit_sha=full_sha,
            commit_message=message,
            hunks=hunks,
        )
        delta = self.harness_commit_delta(bootstrap, work_window)
        return CommitUpdateBuildResult(
            repo_root=root,
            repo_id=resolved_repo_id,
            commit_sha=full_sha,
            commit_message=message,
            diff_hunk_count=len(hunks),
            bootstrap=bootstrap,
            work_window=work_window,
            delta=delta,
        )

    def harness_commit_delta(
        self,
        bootstrap: StructuralRepoBootstrapResult,
        work_window: CommitWorkWindow,
    ) -> GraphUpdateDelta:
        from ....domain.semantic_harness import build_commit_update_delta

        return build_commit_update_delta(bootstrap.graph, work_window)


def _read_commit_metadata(repo_root: Path, commit_sha: str) -> tuple[str, str]:
    result = _run_git(repo_root, "show", "-s", "--format=%H%x00%B", commit_sha)
    full_sha, _, message = result.partition("\x00")
    return full_sha.strip(), message.strip()


def _read_commit_diff(repo_root: Path, commit_sha: str) -> str:
    return _run_git(
        repo_root,
        "show",
        "--format=",
        # Hunk-to-entity mapping needs changed-line spans, not broad context.
        # Wider context belongs in semantic work-window packets, otherwise one
        # small edit can overlap many symbols and suppress relation updates.
        "--unified=0",
        "--find-renames",
        "--find-copies",
        commit_sha,
    )


def _run_git(repo_root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


__all__ = ["CommitUpdateBuildResult", "CommitUpdateService"]
