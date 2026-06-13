from __future__ import annotations

import json

from agent_memory_orchestrator.runtime.cli.commands.semantic_harness import main as harness_main
from agent_memory_orchestrator.runtime.cli.main import main


def test_amo_harness_bootstrap_warms_persistent_graph(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    db_path = tmp_path / "harness.sqlite"

    status = main(
        [
            "amo-harness",
            "bootstrap",
            "--repo",
            str(repo),
            "--repo-id",
            "repo:test",
            "--db-path",
            str(db_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["repo_id"] == "repo:test"
    assert payload["file_count"] == 1
    assert payload["projection_document_count"] > 0
    assert db_path.exists()


def test_direct_amo_harness_script_bootstrap_uses_same_command(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")

    status = harness_main(
        [
            "bootstrap",
            "--repo",
            str(repo),
            "--repo-id",
            "repo:test",
            "--db-path",
            str(tmp_path / "harness.sqlite"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["command"] == "amo-harness bootstrap"
