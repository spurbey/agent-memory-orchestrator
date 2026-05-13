from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.reasoning_graph.decision_packets import DECISION_PACKET_SCHEMA_VERSION
from agent_memory_orchestrator.reasoning_graph.decision_packets import build_decision_packet
from agent_memory_orchestrator.reasoning_graph.decision_packets import build_decision_packets


def _window(commit_id: str = "abc123") -> dict:
    return {
        "window_id": f"window:commit:{commit_id}",
        "session_id": "s1",
        "commit_id": commit_id,
        "full_sha": f"{commit_id}def",
        "parent_shas": ["parent1"],
        "message": "feat(memory): add focused evidence windows",
        "git_changed_files": ["src/window.py", "tests/test_window.py"],
        "git_name_status": [{"status": "A", "path": "src/window.py"}],
        "tool_kind_counts": {"write_patch": 2, "test_or_lint": 1},
        "diagnostics": ["fact_files_include_non_commit_context"],
    }


def _work_change(commit_id: str = "abc123") -> dict:
    return {
        "id": f"work:s1:{commit_id}",
        "session_id": "s1",
        "kind": "WorkChange",
        "summary": "feat(memory): add focused evidence windows. Changed 2 files.",
        "evidence_ids": [f"commit:{commit_id}"],
        "metadata": {
            "commit_id": commit_id,
            "full_sha": f"{commit_id}def",
            "commit_message": "feat(memory): add focused evidence windows",
            "commit_category": "feat",
            "git_changed_files": ["src/window.py", "tests/test_window.py"],
        },
    }


def _chunk(commit_id: str = "abc123", text: str = "assistant: Do not embed the whole transcript.") -> dict:
    return {
        "chunk_id": f"chunk:{commit_id}:1",
        "chunk_type": "code_context",
        "commit_id": commit_id,
        "group_key": "src",
        "git_changed_files": ["src/window.py"],
        "message_event_ids": ["assistant:1", "user:1"],
        "read_fact_event_ids": ["tool:read:1"],
        "write_fact_event_ids": ["tool:write:1"],
        "validation_event_ids": ["tool:test:1"],
        "embedding_event_ids": ["assistant:1", "tool:write:1"],
        "support_event_ids": ["tool:read:1", "tool:write:1", "tool:test:1"],
        "embedding_text": text,
    }


def test_decision_packet_keeps_git_truth_and_allowed_evidence_separate() -> None:
    packet = build_decision_packet(
        commit_window=_window(),
        work_change=_work_change(),
        chunks=[_chunk()],
        extraction_run_id="run1",
    ).as_dict()

    assert packet["schema_version"] == DECISION_PACKET_SCHEMA_VERSION
    assert packet["commit_id"] == "abc123"
    assert packet["work_change"]["kind"] == "WorkChange"
    assert packet["commit_truth"]["git_changed_files"] == ["src/window.py", "tests/test_window.py"]
    assert "assistant:1" in packet["allowed_evidence_event_ids"]
    assert "tool:write:1" in packet["allowed_evidence_event_ids"]
    assert packet["chunks"][0]["write_fact_event_ids"] == ["tool:write:1"]
    assert "write_patch is code evidence" in " ".join(packet["extraction_rules"])


def test_decision_packet_caps_chunk_text_but_preserves_event_ids() -> None:
    packet = build_decision_packet(
        commit_window=_window(),
        work_change=_work_change(),
        chunks=[_chunk(text="x" * 50)],
        extraction_run_id="run1",
        chunk_text_limit=10,
    ).as_dict()

    assert packet["chunks"][0]["embedding_text_excerpt"] == "x" * 10
    assert packet["chunks"][0]["text_truncated"] is True
    assert packet["chunks"][0]["message_event_ids"] == ["assistant:1", "user:1"]


def test_decision_packets_skip_unresolved_or_workless_windows() -> None:
    packets = build_decision_packets(
        commit_windows=[_window("abc123"), {**_window("fake"), "full_sha": ""}, _window("missing")],
        work_changes=[_work_change("abc123")],
        chunks=[_chunk("abc123"), _chunk("missing")],
        extraction_run_id="run1",
    )

    assert len(packets) == 1
    assert packets[0].commit_id == "abc123"


def test_real_stage5c_artifacts_can_build_commit_decision_packet() -> None:
    root = Path(__file__).resolve().parents[1] / ".tmp" / "reasoning-graph-2026-05-11-stage-run"
    windows_path = root / "04c_commit_truth_windows" / "output_commit_truth_windows.json"
    chunks_path = root / "05c_commit_window_embedding_ready_chunks" / "output_embedding_ready_chunks.json"
    work_path = root / "07c_commit_work_changes_full_session" / "output_work_changes.json"
    if not (windows_path.exists() and chunks_path.exists() and work_path.exists()):
        return

    windows = json.loads(windows_path.read_text(encoding="utf-8"))
    chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    work_changes = json.loads(work_path.read_text(encoding="utf-8"))

    packets = build_decision_packets(
        commit_windows=windows,
        work_changes=work_changes,
        chunks=chunks,
        extraction_run_id="extraction_run:019dde30-485e-7461-8568-efcce2b3fb07:stage5c-full-session",
    )

    assert packets
    first = packets[0].as_dict()
    assert first["work_change"]["kind"] == "WorkChange"
    assert first["allowed_evidence_event_ids"]
    assert all(chunk["embedding_text_excerpt"] for chunk in first["chunks"])
