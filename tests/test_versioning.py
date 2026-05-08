from __future__ import annotations

import subprocess
from pathlib import Path

from agent_memory_orchestrator.versioning import LocalGitBackend


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


def _git(cwd: Path, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=str(cwd), text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
