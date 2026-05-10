from __future__ import annotations

import subprocess
from pathlib import Path

from agent_memory_orchestrator.versioning import GitDiffSummary, LocalGitBackend, WorkLedger


def test_local_git_backend_reads_commit_metadata_diff_and_patch_id(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "amo@example.local")
    _git(tmp_path, "config", "user.name", "AMO Test")
    (tmp_path / "src.py").write_text("print('v1')\n", encoding="utf-8")
    _git(tmp_path, "add", "src.py")
    _git(tmp_path, "commit", "-m", "initial graph work")

    backend = LocalGitBackend()
    details = backend.commit_details(cwd=tmp_path)
    diff = backend.diff_summary(cwd=tmp_path)
    patch_id = backend.patch_id(cwd=tmp_path)

    assert details.available is True
    assert details.subject == "initial graph work"
    assert diff.available is True
    assert "src.py" in diff.changed_files
    assert diff.insertions >= 1
    assert patch_id


def test_versioning_package_exports_work_ledger_and_legacy_shim() -> None:
    from agent_memory_orchestrator import work_ledger as legacy_work_ledger

    assert legacy_work_ledger.WorkLedger is WorkLedger
    trace = WorkLedger(_MissingGitBackend()).trace_commit()
    assert trace.patch_id
    assert trace.as_dict()["diff"]["changed_files"] == ["missing.py"]


class _MissingGitBackend:
    def commit_details(self, commit: str = "HEAD", cwd: str | Path | None = None):
        return LocalGitBackend().commit_details(commit="missing", cwd=Path.cwd())

    def diff_summary(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitDiffSummary:
        return GitDiffSummary(available=False, changed_files=["missing.py"], insertions=1, deletions=0)

    def patch_id(self, commit: str = "HEAD", cwd: str | Path | None = None) -> str:
        return ""


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
