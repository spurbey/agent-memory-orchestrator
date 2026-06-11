from __future__ import annotations

import subprocess

import pytest

from agent_memory_orchestrator.application.services.semantic_harness import RepoBootstrapOptions
from agent_memory_orchestrator.application.services.semantic_harness import StructuralHarnessService
from agent_memory_orchestrator.application.services.semantic_harness import read_repo_source_files
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest


def test_read_repo_source_files_filters_generated_binary_and_large_files(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "src" / "main.py").write_text("def run():\n    return True\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Demo\n", encoding="utf-8")
    (tmp_path / ".git" / "config").write_text("ignored", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg.js").write_text("ignored", encoding="utf-8")
    (tmp_path / "image.png").write_bytes(b"\x00PNG")
    (tmp_path / "large.py").write_text("x = 1\n" * 20, encoding="utf-8")

    snapshot = read_repo_source_files(
        tmp_path,
        RepoBootstrapOptions(max_file_bytes=40),
    )

    paths = {source.path for source in snapshot.files}
    skipped = {(item["path"], item["reason"]) for item in snapshot.skipped}

    assert paths == {"README.md", "src/main.py"}
    assert ("image.png", "unsupported_suffix") in skipped
    assert ("large.py", "file_too_large") in skipped
    assert not any("node_modules" in path for path in paths)
    assert not any(".git" in path for path in paths)


def test_read_repo_source_files_prefers_git_tracked_files(tmp_path) -> None:
    if subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0:
        pytest.skip("git unavailable")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "tracked.py").write_text("def tracked():\n    return True\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text("def untracked():\n    return False\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "src/tracked.py"], cwd=tmp_path, check=True, capture_output=True)

    snapshot = read_repo_source_files(tmp_path)

    assert tuple(source.path for source in snapshot.files) == ("src/tracked.py",)


def test_structural_service_bootstrap_repo_and_query_exact_anchor(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        "class AuthSession:\n    def refresh(self):\n        return True\n",
        encoding="utf-8",
    )

    service = StructuralHarnessService()
    result = service.bootstrap_repo(tmp_path, repo_id="repo:test")
    response = service.query(
        result.graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect auth refresh",
            files=("src/auth.py",),
            symbols=("AuthSession.refresh",),
            max_cards=2,
        ),
    )

    assert result.file_count == 1
    assert result.skipped_count == 0
    assert response.status == "partial_structural"
    assert [card.type for card in response.cards] == ["next_file", "symbol_context"]
    assert response.next_actions[0].action_type == "inspect_file"
