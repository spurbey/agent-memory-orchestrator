from __future__ import annotations

import subprocess

import pytest

from agent_memory_orchestrator.application.services.semantic_harness import CommitUpdateEvalCase
from agent_memory_orchestrator.application.services.semantic_harness import CommitUpdateEvalService


def test_commit_update_eval_reports_passed_commit_shape(tmp_path) -> None:
    sha = _make_two_commit_repo(tmp_path)

    report = CommitUpdateEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            CommitUpdateEvalCase(
                case_id="login_change",
                commit_sha=sha,
                required_node_kinds=("Commit", "Hunk", "FileVersion", "SymbolVersion"),
                required_edge_kinds=("VERSION_OF", "CHANGED_IN", "MAPS_TO_SYMBOL"),
                required_mapping_statuses=("mapped",),
            ),
        ),
    )

    assert report.passed is True
    assert report.contract_judgment["qwen_used"] is False
    assert report.contract_judgment["storage_mutated"] is False
    assert report.cases[0].diff_hunk_count == 1
    assert report.cases[0].mapping_status_counts == {"mapped": 1}
    assert report.as_dict()["cases"][0]["passed"] is True


def test_commit_update_eval_reports_missing_expected_shape(tmp_path) -> None:
    sha = _make_two_commit_repo(tmp_path)

    report = CommitUpdateEvalService().evaluate_repo(
        tmp_path,
        repo_id="repo:test",
        cases=(
            CommitUpdateEvalCase(
                case_id="missing_relation_occurrence",
                commit_sha=sha,
                required_node_kinds=("RelationOccurrence",),
                required_mapping_statuses=("review_only",),
            ),
        ),
    )

    assert report.passed is False
    assert "missing_node_kind:RelationOccurrence" in report.cases[0].failure_reasons
    assert "missing_mapping_status:review_only" in report.cases[0].failure_reasons


def _make_two_commit_repo(tmp_path) -> str:
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
    target.write_text("def login():\n    return True\n", encoding="utf-8")
    _git(tmp_path, "add", "src/auth.py")
    _git(tmp_path, "commit", "-m", "fix auth login")
    return _git(tmp_path, "rev-parse", "HEAD").strip()


def _git(cwd, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        encoding="utf-8",
    )
    return result.stdout
