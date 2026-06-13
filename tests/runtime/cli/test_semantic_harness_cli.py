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


def test_amo_harness_shadow_replay_reads_post_tool_use_rows(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    db_path = tmp_path / "harness.sqlite"
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(
        json.dumps(
            {
                "payload": {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "shell_command",
                    "tool_input": {"command": "Get-Content src/auth.py"},
                    "tool_response": "def login():\n    return True\n",
                    "cwd": str(repo),
                    "session_id": "session-1",
                    "turn_id": "turn-1",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bootstrap_status = main(
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
    _ = capsys.readouterr()

    status = main(
        [
            "amo-harness",
            "shadow-replay",
            "--repo-id",
            "repo:test",
            "--evidence",
            str(evidence_path),
            "--db-path",
            str(db_path),
            "--out",
            str(tmp_path / "shadow.json"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert bootstrap_status == 0
    assert status == 0
    assert payload["ok"] is True
    assert payload["shadow_only"] is True
    assert payload["record_count"] == 1
    assert payload["metrics"]["token_overhead_p95"] == 0
    assert payload["metrics"]["all_token_overhead_p95"] > 0
    assert payload["metrics"]["acceptance_thresholds"]["p95_shadow_latency_ms"] == 500
    assert payload["metrics"]["by_tool_kind"]["file_read"]["suppress_rate"] == 1.0
    assert payload["records"][0]["decision"]["would_attach"] is False
    assert "redundant_file_read_card" in payload["records"][0]["decision"]["suppression_reasons"]
    assert payload["records"][0]["decision"]["would_replace"] is False
    assert payload["records"][0]["captured"]["raw_output_hash"]
    assert (tmp_path / "shadow.json").exists()


def test_amo_harness_shadow_replay_missing_graph_reports_unavailable(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    evidence_path = tmp_path / "evidence.jsonl"
    evidence_path.write_text(
        json.dumps(
            {
                "payload": {
                    "hook_event_name": "PostToolUse",
                    "tool_name": "shell_command",
                    "tool_input": {"command": "Get-Content src/auth.py"},
                    "tool_response": "def login():\n    return True\n",
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )

    status = harness_main(
        [
            "shadow-replay",
            "--repo-id",
            "repo:missing",
            "--evidence",
            str(evidence_path),
            "--db-path",
            str(tmp_path / "harness.sqlite"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["record_count"] == 1
    assert payload["records"][0]["harness_response"]["status"] == "unavailable"
    assert payload["records"][0]["decision"]["would_attach"] is False
