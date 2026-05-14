from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.reasoning_graph import build_reasoning_work_packets_from_view
from agent_memory_orchestrator.reasoning_graph import is_strict_validation_fact
from agent_memory_orchestrator.reasoning_graph import packet_json_contains_raw_internal_ids


def _view() -> dict:
    return {
        "stage": "02b_reasoning_evidence_view_tight",
        "user_problems": [
            {"ref": "E0001", "timestamp": "2026-01-01T00:00:01Z", "request": "Need focused evidence windows for reasoning graph."},
            {"ref": "E0002", "timestamp": "2026-01-01T00:00:10Z", "request": "Unrelated user text."},
        ],
        "assistant_reasoning": [
            {"ref": "E0003", "timestamp": "2026-01-01T00:00:02Z", "statement": "Use staged modules before graph creation."},
            {"ref": "E0004", "timestamp": "2026-01-01T00:00:12Z", "statement": "Other unrelated note."},
        ],
        "commit_facts": [
            {
                "ref": "E0005",
                "timestamp": "2026-01-01T00:00:20Z",
                "commit_id": "abc1234",
                "message_from_output": "feat(reasoning-graph): add focused evidence windows",
                "git_truth": {
                    "commit_id": "abc1234",
                    "full_sha": "abc123456789",
                    "resolved": True,
                    "message": "feat(reasoning-graph): add focused evidence windows",
                    "changed_files": ["src/window.py", "tests/test_window.py"],
                    "name_status": [{"status": "A", "path": "src/window.py"}],
                },
            },
            {
                "ref": "E0006",
                "timestamp": "2026-01-01T00:00:30Z",
                "commit_id": "fake123",
                "message_from_output": "fake commit",
                "git_truth": {"commit_id": "fake123", "resolved": False},
            },
        ],
        "validation_facts": [
            {
                "ref": "E0007",
                "timestamp": "2026-01-01T00:00:18Z",
                "status": "pass",
                "command": "python -m pytest tests/test_window.py -q",
                "output_preview": "1 passed",
            },
            {
                "ref": "E0008",
                "timestamp": "2026-01-01T00:00:19Z",
                "status": "unknown",
                "command": "Get-Content src/window.py",
                "output_preview": "file contents",
            },
        ],
    }


def test_strict_validation_gates_test_commands_from_support_commands() -> None:
    assert is_strict_validation_fact({"command": "python -m pytest tests -q"}) is True
    assert is_strict_validation_fact({"command": "ruff check src tests"}) is True
    assert is_strict_validation_fact({"command": "Get-Content src/app.py"}) is False
    assert is_strict_validation_fact({"command": "git show --stat HEAD"}) is False


def test_work_packet_builder_creates_commit_packets_and_quarantines_fake_commits() -> None:
    result = build_reasoning_work_packets_from_view(_view())

    assert result.quality["packet_count"] == 1
    assert result.quality["quarantined_commit_count"] == 1
    assert result.quality["strict_validation_fact_count"] == 1
    assert result.quality["rejected_validation_like_support_count"] == 1
    assert result.quality["packets_with_raw_internal_ids_in_main_json"] is False

    packet = result.packets[0]
    assert packet["packet_id"] == "WP0001"
    assert packet["commit"]["short_sha"] == "abc1234"
    assert packet["commit"]["changed_file_sample"] == ["src/window.py", "tests/test_window.py"]
    assert packet["problem_refs"][0]["ref"] == "E0001"
    assert packet["rationale_refs"][0]["ref"] == "E0003"
    assert packet["validation_refs"][0]["ref"] == "E0007"


def test_packet_raw_internal_id_detector_rejects_transcript_and_call_ids() -> None:
    assert packet_json_contains_raw_internal_ids({"ref": "E0001"}) is False
    assert packet_json_contains_raw_internal_ids({"ref": "transcript:session:tool_use:call_abc"}) is True
    assert packet_json_contains_raw_internal_ids({"ref": "tool_result:call_abcdefghijk"}) is True


def test_real_stage2b_view_builds_strict_work_packets() -> None:
    root = Path(__file__).resolve().parents[1]
    view_path = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14" / "02b_reasoning_evidence_view_tight" / "reasoning_evidence_view.json"
    if not view_path.exists():
        return

    view = json.loads(view_path.read_text(encoding="utf-8"))
    result = build_reasoning_work_packets_from_view(view)

    assert result.quality["packet_count"] == 59
    assert result.quality["quarantined_commit_count"] == 1
    assert result.quality["packets_without_problem_refs"] == 0
    assert result.quality["packets_without_rationale_refs"] == 0
    assert result.quality["packets_with_raw_internal_ids_in_main_json"] is False
    assert result.packets[0]["commit"]["short_sha"] == "61b51d9"
    assert result.packets[-1]["commit"]["short_sha"] == "0b8f7bd"
    assert "167bb3a" in {packet["commit"]["short_sha"] for packet in result.packets}
