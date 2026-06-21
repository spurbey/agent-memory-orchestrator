from __future__ import annotations

import json

from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.application.services.semantic_harness import InMemoryHarnessGraphRepository
from agent_memory_orchestrator.domain.semantic_harness.semantic_facts import AGENT_CHECKPOINT_SCHEMA_VERSION
from agent_memory_orchestrator.runtime.cli.commands.semantic_harness import main as harness_main
from agent_memory_orchestrator.runtime.cli.main import main


class _FakeHelixRepository:
    repository = InMemoryHarnessGraphRepository()

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def __enter__(self):
        return self.repository

    def __exit__(self, *_args) -> None:
        return None


def _use_fake_helix(monkeypatch) -> _FakeHelixRepository:
    import agent_memory_orchestrator.runtime.cli.commands.semantic_checkpoint as checkpoint_command
    import agent_memory_orchestrator.runtime.cli.commands.semantic_harness as harness_command

    fake = _FakeHelixRepository()
    fake.repository = InMemoryHarnessGraphRepository()
    _FakeHelixRepository.repository = fake.repository
    monkeypatch.setattr(harness_command, "HelixHarnessGraphRepository", _FakeHelixRepository)
    monkeypatch.setattr(checkpoint_command, "HelixHarnessGraphRepository", _FakeHelixRepository)
    return fake


def test_amo_harness_bootstrap_warms_persistent_graph(tmp_path, monkeypatch, capsys) -> None:
    fake = _use_fake_helix(monkeypatch)
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    status = main(
        [
            "amo-harness",
            "bootstrap",
            "--repo",
            str(repo),
            "--repo-id",
            "repo:test",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["repo_id"] == "repo:test"
    assert payload["file_count"] == 1
    assert payload["projection_document_count"] > 0
    assert payload["backend"] == "helix"
    assert fake.repository.load("repo:test") is not None


def test_direct_amo_harness_script_bootstrap_uses_same_command(tmp_path, monkeypatch, capsys) -> None:
    _use_fake_helix(monkeypatch)
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
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["command"] == "amo-harness bootstrap"


def test_amo_harness_shadow_replay_reads_post_tool_use_rows(tmp_path, monkeypatch, capsys) -> None:
    _use_fake_helix(monkeypatch)
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
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
    _use_fake_helix(monkeypatch)
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
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["record_count"] == 1
    assert payload["records"][0]["harness_response"]["status"] == "unavailable"
    assert payload["records"][0]["decision"]["would_attach"] is False


def test_semantic_checkpoint_ingest_cli_writes_pending_artifacts(tmp_path, monkeypatch, capsys) -> None:
    fake = _use_fake_helix(monkeypatch)
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "amo"))
    graph = StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(id="repo:test", kind="Repo", label="repo:test", repo_id="repo:test"),
            HarnessNode(
                id="file:repo:test:src/auth.py",
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                metadata={"path": "src/auth.py"},
            ),
        ),
        edges=(),
    )
    fake.repository.replace_from_graph(graph)
    checkpoint_file = tmp_path / "semantic_checkpoint.json"
    checkpoint_file.write_text(json.dumps(_checkpoint_payload()), encoding="utf-8")
    out_dir = tmp_path / "review"

    status = main(
        [
            "semantic-checkpoint",
            "ingest",
            "--file",
            str(checkpoint_file),
            "--repo-id",
            "repo:test",
            "--out-dir",
            str(out_dir),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert status == 0
    assert payload["ok"] is True
    assert payload["command"] == "semantic-checkpoint ingest"
    assert payload["mode"] == "pending"
    assert payload["summary"]["graph_mutated"] is False
    assert (out_dir / "review_result.json").exists()


def _checkpoint_payload() -> dict[str, object]:
    return {
        "schema_version": AGENT_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": "checkpoint-cli",
        "parent_session_id": "session-1",
        "repo_root": "C:/repo",
        "base_commit": "abc",
        "head_commit": "def",
        "checkpoint_time": "2026-06-18T12:00:00Z",
        "session_goal": "Preserve auth semantics.",
        "work_windows": [
            {
                "window_id": "window-1",
                "commit_sha": "def",
                "commit_message": "Preserve login behavior",
                "changed_files": ["src/auth.py"],
                "tests_run": [],
                "semantic_facts": [
                    {
                        "fact_type": "implementation_rationale",
                        "text": "Login fallback behavior is kept for anonymous-session compatibility.",
                        "anchors": [{"path": "src/auth.py"}],
                        "source_refs": [
                            {
                                "kind": "diff",
                                "commit_sha": "def",
                                "path": "src/auth.py",
                                "line_start": 1,
                                "line_end": 2,
                                "excerpt": "return None",
                            }
                        ],
                        "derivability": "requires_agent_session_history",
                        "source_kind": "agent_session",
                        "source_span": "validated_committed",
                        "confidence": 0.8,
                    }
                ],
                "rejected_approaches": [],
                "open_questions": [],
            }
        ],
    }
