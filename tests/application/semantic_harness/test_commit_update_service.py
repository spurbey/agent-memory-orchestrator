from __future__ import annotations

import subprocess

import pytest

from agent_memory_orchestrator.application.services.semantic_harness import CommitUpdateService
from agent_memory_orchestrator.domain.semantic_harness import commit_id


def test_commit_update_service_builds_delta_from_real_git_commit(tmp_path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("git unavailable")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "auth.py"
    target.write_text("def login():\n    return False\n", encoding="utf-8")
    _git(tmp_path, "add", "src/auth.py")
    _git(tmp_path, "commit", "-m", "add auth login")
    first_sha = _git(tmp_path, "rev-parse", "HEAD").strip()
    target.write_text("def login():\n    return True\n", encoding="utf-8")
    _git(tmp_path, "add", "src/auth.py")
    _git(tmp_path, "commit", "-m", "fix auth login")
    second_sha = _git(tmp_path, "rev-parse", "HEAD").strip()

    result = CommitUpdateService().build_delta_for_commit(
        tmp_path,
        second_sha,
        repo_id="repo:test",
        session_id="session-1",
    )

    assert result.commit_sha == second_sha
    assert result.commit_message == "fix auth login"
    assert result.diff_hunk_count == 1
    assert result.delta.commit_id == commit_id("repo:test", second_sha)
    assert result.work_window.session_id == "session-1"
    assert {mapping.status for mapping in result.delta.hunk_mappings} == {"mapped"}
    assert "SymbolVersion" in {node.kind for node in result.delta.created_nodes}
    assert "MAPS_TO_SYMBOL" in {edge.kind for edge in result.delta.created_edges}
    assert first_sha != second_sha


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return result.stdout
