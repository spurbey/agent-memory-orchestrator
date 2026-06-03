from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.domain.evidence.views import build_reasoning_evidence_view
from agent_memory_orchestrator.domain.evidence.views import classify_tool
from agent_memory_orchestrator.domain.evidence.views import clean_user_request
from agent_memory_orchestrator.domain.evidence.views import keep_assistant_reasoning
from agent_memory_orchestrator.domain.evidence.views import keep_user_request
from agent_memory_orchestrator.domain.evidence.views import reasoning_evidence_view_contains_raw_internal_ids
from agent_memory_orchestrator.domain.evidence.views import write_reasoning_evidence_view_artifacts
from agent_memory_orchestrator.domain.reasoning import build_reasoning_work_packets_from_view


def test_clean_user_request_removes_ide_context_but_keeps_request() -> None:
    text = """# Context from my IDE setup:

## Active file: src/app.py

## Open tabs:
- src/app.py

## My request for Codex:
Build the reasoning graph stage from the full session.
"""

    cleaned = clean_user_request(text)

    assert cleaned == "Build the reasoning graph stage from the full session."
    assert keep_user_request(cleaned) is True


def test_support_only_classification_keeps_patch_text_out_of_validation() -> None:
    patch_command = """*** Begin Patch
*** Add File: tests/test_example.py
+def test_it_passes():
+    assert True
*** End Patch
"""
    validation_command = "python -m pytest tests/test_example.py -q"

    assert classify_tool("apply_patch", patch_command) == "code_write"
    assert classify_tool("shell_command", patch_command, "Success. Updated the following files") == "code_write"
    assert classify_tool("shell_command", validation_command) == "validation"
    assert classify_tool("shell_command", "Get-Content tests/test_example.py") == "read_search"


def test_assistant_reasoning_gate_keeps_plans_and_drops_noise() -> None:
    assert keep_assistant_reasoning("ok") is False
    assert keep_assistant_reasoning("I will build this as staged modules before graph creation.") is True
    assert keep_assistant_reasoning("The root cause is that patch text was treated as validation.") is True


def test_evidence_view_scopes_transcript_to_raw_turn_windows(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _task_event("task_started", "turn-old"),
                _message("user", "Old unrelated bootstrap work should not be imported."),
                _tool_call("call_old", "git commit -m old"),
                _tool_output("call_old", "[main abc1234] chore: old unrelated commit"),
                _task_event("task_complete", "turn-old"),
                _task_event("task_started", "turn-keep"),
                _message("user", "Fix the scoped transcript evidence boundary."),
                _message("assistant", "I will fix this because resumed transcripts contain older session blocks."),
                _tool_call("call_new", "git commit -m scoped"),
                _tool_output("call_new", "[main def5678] fix: scope transcript evidence"),
                _task_event("task_complete", "turn-keep"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_text(
        json.dumps(
            {
                "event_name": "user_prompt_submit",
                "session_id": "s1",
                "payload": {"turn_id": "turn-keep", "transcript_path": str(transcript)},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    build = build_reasoning_evidence_view(raw_path, repo_root=tmp_path)

    assert build.quality["transcript_scope"] == "raw_turn_window"
    assert build.quality["raw_turn_id_count"] == 1
    assert build.quality["matched_turn_start_count"] == 1
    assert build.quality["matched_turn_complete_count"] == 1
    assert build.quality["unscoped_transcript_line_count"] == 5
    assert [item["commit_id"] for item in build.view["commit_facts"]] == ["def5678"]
    assert [item["request"] for item in build.view["user_problems"]] == ["Fix the scoped transcript evidence boundary."]
    assert "old unrelated" not in json.dumps(build.view, ensure_ascii=False).lower()


def test_evidence_view_falls_back_to_full_transcript_without_raw_turn_ids(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                _task_event("task_started", "turn-old"),
                _message("user", "Old full transcript fallback work."),
                _tool_call("call_old", "git commit -m old"),
                _tool_output("call_old", "[main abc1234] chore: old fallback commit"),
                _task_event("task_complete", "turn-old"),
                _task_event("task_started", "turn-new"),
                _message("user", "New full transcript fallback work."),
                _tool_call("call_new", "git commit -m new"),
                _tool_output("call_new", "[main def5678] fix: new fallback commit"),
                _task_event("task_complete", "turn-new"),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_text(
        json.dumps({"event_name": "session_start", "session_id": "s1", "payload": {"transcript_path": str(transcript)}}) + "\n",
        encoding="utf-8",
    )

    build = build_reasoning_evidence_view(raw_path, repo_root=tmp_path)

    assert build.quality["transcript_scope"] == "full_transcript"
    assert build.quality["raw_turn_id_count"] == 0
    assert [item["commit_id"] for item in build.view["commit_facts"]] == ["abc1234", "def5678"]


def test_real_stage2b_evidence_view_matches_reset_artifact(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[3]
    raw_path = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14" / "01_raw_jsonl_whole_file" / "input_raw_2026-05-11.full.jsonl"
    stage2b_dir = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14" / "02b_reasoning_evidence_view_tight"
    golden_path = stage2b_dir / "reasoning_evidence_view.json"
    support_path = stage2b_dir / "support_ref_map.json"
    if not raw_path.exists() or not golden_path.exists() or not support_path.exists():
        return

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    support = json.loads(support_path.read_text(encoding="utf-8"))
    max_line = max(int(item["line_no"]) for item in support)
    build = build_reasoning_evidence_view(raw_path, repo_root=root, max_transcript_line=max_line, scope_to_raw_turns=False)

    assert build.quality["raw_record_count"] == golden["raw_record_count"] == 698
    assert build.quality["user_problem_count"] == len(golden["user_problems"]) == 368
    assert build.quality["assistant_reasoning_count"] == len(golden["assistant_reasoning"]) == 712
    assert build.quality["commit_fact_count"] == len(golden["commit_facts"]) == 60
    assert build.quality["validation_fact_count"] == len(golden["validation_facts"]) == 360
    assert build.quality["code_write_support_count"] == golden["code_write_support_count"] == 704
    assert build.quality["support_ref_count"] == 3026
    assert build.quality["max_transcript_line"] == max_line == 18851
    assert build.quality["main_view_has_raw_internal_ids"] is False
    assert reasoning_evidence_view_contains_raw_internal_ids(build.view) is False

    assert [item["commit_id"] for item in build.view["commit_facts"]] == [item["commit_id"] for item in golden["commit_facts"]]
    assert build.view["user_problems"][0] == golden["user_problems"][0]
    assert build.view["assistant_reasoning"][0] == golden["assistant_reasoning"][0]
    assert build.view["validation_facts"][0] == golden["validation_facts"][0]

    packet_build = build_reasoning_work_packets_from_view(build.view)
    assert packet_build.quality["packet_count"] == 59
    assert packet_build.quality["quarantined_commit_count"] == 1
    assert packet_build.quality["packets_with_raw_internal_ids_in_main_json"] is False

    write_reasoning_evidence_view_artifacts(build, tmp_path)
    assert (tmp_path / "reasoning_evidence_view.json").exists()
    assert (tmp_path / "support_ref_map.json").exists()
    assert (tmp_path / "stage2b_inventory.json").exists()


def _task_event(event_type: str, turn_id: str) -> dict[str, object]:
    return {"timestamp": "2026-05-26T00:00:00Z", "type": "event_msg", "payload": {"type": event_type, "turn_id": turn_id}}


def _message(role: str, text: str) -> dict[str, object]:
    return {"timestamp": "2026-05-26T00:00:01Z", "type": "response_item", "payload": {"type": "message", "role": role, "content": [{"text": text}]}}


def _tool_call(call_id: str, command: str) -> dict[str, object]:
    return {
        "timestamp": "2026-05-26T00:00:02Z",
        "type": "response_item",
        "payload": {"type": "function_call", "call_id": call_id, "name": "shell_command", "arguments": json.dumps({"command": command})},
    }


def _tool_output(call_id: str, output: str) -> dict[str, object]:
    return {"timestamp": "2026-05-26T00:00:03Z", "type": "response_item", "payload": {"type": "function_call_output", "call_id": call_id, "output": output}}

