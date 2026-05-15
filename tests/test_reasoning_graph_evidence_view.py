from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.reasoning_graph import build_reasoning_evidence_view
from agent_memory_orchestrator.reasoning_graph import build_reasoning_work_packets_from_view
from agent_memory_orchestrator.reasoning_graph import classify_tool
from agent_memory_orchestrator.reasoning_graph import clean_user_request
from agent_memory_orchestrator.reasoning_graph import keep_assistant_reasoning
from agent_memory_orchestrator.reasoning_graph import keep_user_request
from agent_memory_orchestrator.reasoning_graph import reasoning_evidence_view_contains_raw_internal_ids
from agent_memory_orchestrator.reasoning_graph import write_reasoning_evidence_view_artifacts


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


def test_real_stage2b_evidence_view_matches_reset_artifact(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    raw_path = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14" / "01_raw_jsonl_whole_file" / "input_raw_2026-05-11.full.jsonl"
    stage2b_dir = root / ".tmp" / "reasoning-graph-v2-reset-2026-05-14" / "02b_reasoning_evidence_view_tight"
    golden_path = stage2b_dir / "reasoning_evidence_view.json"
    support_path = stage2b_dir / "support_ref_map.json"
    if not raw_path.exists() or not golden_path.exists() or not support_path.exists():
        return

    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    support = json.loads(support_path.read_text(encoding="utf-8"))
    max_line = max(int(item["line_no"]) for item in support)
    build = build_reasoning_evidence_view(raw_path, repo_root=root, max_transcript_line=max_line)

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
