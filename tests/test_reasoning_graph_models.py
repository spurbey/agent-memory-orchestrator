from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_memory_orchestrator.reasoning_graph import CodeNode
from agent_memory_orchestrator.reasoning_graph import DecisionUnit
from agent_memory_orchestrator.reasoning_graph import ExtractionRun
from agent_memory_orchestrator.reasoning_graph import TimelineEvent
from agent_memory_orchestrator.reasoning_graph import validate_graph_object
from agent_memory_orchestrator.reasoning_graph import validate_status_transition


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


def test_rejects_answer_grade_node_without_evidence_ids() -> None:
    node = DecisionUnit(
        id="decision:s1:missing-evidence",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw1",
        summary="Use typed graph validators before Kuzu writes.",
        evidence_ids=(),
    )

    report = validate_graph_object(node)

    assert report.ok is False
    assert "answer_grade_missing_evidence" in _codes(report)


def test_rejects_qwen_derived_node_without_extraction_run_id() -> None:
    node = DecisionUnit(
        id="decision:s1:qwen",
        session_id="s1",
        extraction_run_id="",
        summary="Qwen extracted a decision.",
        evidence_ids=("raw1",),
        source="qwen",
        qwen_call="decision_extraction_fallback",
        confidence=0.9,
    )

    report = validate_graph_object(node)

    assert report.ok is False
    assert "qwen_missing_extraction_run" in _codes(report)
    assert "answer_grade_missing_extraction_run" in _codes(report)


def test_rejects_low_confidence_qwen_output() -> None:
    node = DecisionUnit(
        id="decision:s1:qwen-low",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw1",
        summary="Qwen extracted a low confidence decision.",
        evidence_ids=("raw1",),
        source="qwen",
        qwen_call="decision_extraction_fallback",
        confidence=0.4,
    )

    report = validate_graph_object(node)

    assert report.ok is False
    assert "qwen_low_confidence" in _codes(report)


def test_rejects_invalid_status_transition() -> None:
    report = validate_status_transition("committed", "draft")

    assert report.ok is False
    assert "invalid_status_transition" in _codes(report)


def test_accepts_valid_code_node_with_provenance() -> None:
    node = CodeNode(
        id="code:s1:install-service:1",
        session_id="s1",
        extraction_run_id="extraction_run:s1:raw1",
        file_path="src/agent_memory_orchestrator/install_service.py",
        ast_type="function_definition",
        line_start=10,
        line_end=22,
        content="def apply_install_plan():\n    pass",
        commit_id="c5326f8",
        evidence_ids=("raw1",),
    )

    report = validate_graph_object(node)

    assert report.ok is True


def test_extraction_run_requires_evidence_ids() -> None:
    run = ExtractionRun.create(session_id="s1", evidence_ids=())

    report = validate_graph_object(run)

    assert report.ok is False
    assert "extraction_run_missing_evidence" in _codes(report)


def test_real_evidence_record_builds_timeline_event_without_graph_mutation() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    if not evidence_path.exists():
        pytest.skip("real AMO evidence file is not available in this environment")

    target = "raw_639e2963e72e4e3bb063042eeb221afd"
    raw = None
    with evidence_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            item = json.loads(line)
            if item.get("id") == target:
                raw = item
                break
    if raw is None:
        pytest.skip(f"selected real evidence id {target} is not available")

    event = TimelineEvent.from_raw_evidence(raw)

    assert event.session_id == "019e08eb-8f1f-7381-8f25-59344c4ac8a9"
    assert event.evidence_id == target
    assert event.event_type == "post_tool_use"
    assert event.tool_name == "apply_patch"
    assert "install_service.py" in "\n".join(event.files)
    assert event.transcript_path.endswith("019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl")
