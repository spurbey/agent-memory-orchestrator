from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from agent_memory_orchestrator.reasoning_graph import CodeNode
from agent_memory_orchestrator.reasoning_graph import ChunkingConfig
from agent_memory_orchestrator.reasoning_graph import DecisionUnit
from agent_memory_orchestrator.reasoning_graph import ExtractionRun
from agent_memory_orchestrator.reasoning_graph import HashEmbeddingProvider
from agent_memory_orchestrator.reasoning_graph import TimelineGraph
from agent_memory_orchestrator.reasoning_graph import TimelineEvent
from agent_memory_orchestrator.reasoning_graph import build_timeline
from agent_memory_orchestrator.reasoning_graph import build_decision_threads
from agent_memory_orchestrator.reasoning_graph import load_codex_transcript_events
from agent_memory_orchestrator.reasoning_graph import semantic_drift_boundary
from agent_memory_orchestrator.reasoning_graph import validate_graph_object
from agent_memory_orchestrator.reasoning_graph import validate_status_transition


def _codes(report) -> set[str]:
    return {issue.code for issue in report.issues}


class _KeywordEmbedder:
    def embed(self, text: str) -> list[float]:
        lowered = text.lower()
        if "dashboard" in lowered or "css" in lowered:
            return [0.0, 1.0]
        if "api" in lowered or "client" in lowered:
            return [0.2, 0.8]
        return [1.0, 0.0]


def _event(event_id: str, event_type: str, content: str, *, files: tuple[str, ...] = ()) -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        session_id="s1",
        event_type=event_type,
        timestamp=f"2026-05-08T00:00:0{event_id[-1]}.000Z",
        content=content,
        files=files,
    )


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


def test_transcript_imports_assistant_message(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:00.000Z",
                        "type": "session_meta",
                        "payload": {"id": "s1", "cwd": "repo"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:01.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "I found the issue in install_service.py."}],
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    events = load_codex_transcript_events(transcript, session_id="s1")

    assert [event.event_type for event in events] == ["session_start", "agent_message"]
    assert events[1].content == "I found the issue in install_service.py."


def test_timeline_orders_and_dedupes_by_call_id(tmp_path: Path) -> None:
    transcript = tmp_path / "rollout.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:03.000Z",
                        "type": "response_item",
                        "payload": {"type": "function_call_output", "call_id": "call1", "output": "ok"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:01.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call1",
                            "name": "shell_command",
                            "arguments": "{\"command\":\"echo ok\"}",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-08T00:00:02.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "function_call",
                            "call_id": "call1",
                            "name": "shell_command",
                            "arguments": "{\"command\":\"echo ok duplicate\"}",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    timeline = build_timeline(session_id="s1", transcript_paths=[transcript])

    assert [event.event_type for event in timeline.events] == ["tool_use", "tool_result"]
    assert len(timeline.edges) == 1
    assert timeline.edges[0].kind == "FOLLOWED_BY"


def test_real_session_timeline_contains_messages_tools_and_stop_events() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    transcript_path = (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "09"
        / "rollout-2026-05-09T00-33-35-019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl"
    )
    if not evidence_path.exists() or not transcript_path.exists():
        pytest.skip("selected real AMO evidence/transcript is not available in this environment")

    timeline = build_timeline(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_paths=[evidence_path],
        transcript_paths=[transcript_path],
    )

    event_types = timeline.event_types()
    assert "user_message" in event_types
    assert "agent_message" in event_types
    assert "tool_use" in event_types
    assert "tool_result" in event_types
    assert "post_tool_use" in event_types
    assert "stop" in event_types
    assert len(timeline.edges) == max(0, len(timeline.events) - 1)
    assert any("install_service.py" in "\n".join(event.files) for event in timeline.events)


def test_chunking_creates_boundary_on_file_switch() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "tool_result", "updated installer", files=("src/install_service.py",)),
            _event("e2", "tool_result", "updated hook", files=("src/hook.py",)),
        ),
        edges=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 2
    assert result.chunks[0].diagnostics == ("file_switch",)


def test_chunking_creates_boundary_on_explicit_transition_phrase() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "agent_message", "I fixed the installer path."),
            _event("e2", "agent_message", "Now let me check the dashboard CSS."),
        ),
        edges=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 2
    assert "explicit_transition" in result.chunks[0].diagnostics


def test_semantic_drift_threshold_creates_boundary_below_065() -> None:
    drift, score, reason = semantic_drift_boundary(
        ["installer hook", "install service", "capture hook"],
        "dashboard css layout",
        embedder=_KeywordEmbedder(),
        config=ChunkingConfig(semantic_drift_threshold=0.65),
    )

    assert drift is True
    assert score == 0.0
    assert reason == "semantic_drift"


def test_revisit_threshold_merges_same_file_topic() -> None:
    timeline = TimelineGraph(
        session_id="s1",
        events=(
            _event("e1", "agent_message", "installer capture hook", files=("src/install_service.py",)),
            _event("e2", "agent_message", "dashboard css", files=("src/web/amo.css",)),
            _event("e3", "agent_message", "back to install service hook", files=("src/install_service.py",)),
        ),
        edges=(),
    )
    run = ExtractionRun.create(session_id="s1", evidence_ids=("raw1",))

    result = build_decision_threads(timeline, extraction_run=run, embedder=_KeywordEmbedder())

    assert len(result.chunks) == 3
    assert len(result.threads) == 2
    assert result.threads[0].metadata["continued_chunk_count"] == 1


def test_missing_embeddings_record_fallback_without_semantic_boundary() -> None:
    drift, score, reason = semantic_drift_boundary(
        ["installer hook", "install service", "capture hook"],
        "dashboard css layout",
        embedder=None,
    )

    assert drift is False
    assert score is None
    assert reason == "embedding_status=missing"


def test_real_session_builds_decision_threads_from_selected_evidence() -> None:
    home = Path(os.environ.get("USERPROFILE", ""))
    evidence_path = home / ".agent-memory-orchestrator" / ".evidence" / "2026-05-08.jsonl"
    transcript_path = (
        home
        / ".codex"
        / "sessions"
        / "2026"
        / "05"
        / "09"
        / "rollout-2026-05-09T00-33-35-019e08eb-8f1f-7381-8f25-59344c4ac8a9.jsonl"
    )
    if not evidence_path.exists() or not transcript_path.exists():
        pytest.skip("selected real AMO evidence/transcript is not available in this environment")
    timeline = build_timeline(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_paths=[evidence_path],
        transcript_paths=[transcript_path],
    )
    run = ExtractionRun.create(
        session_id="019e08eb-8f1f-7381-8f25-59344c4ac8a9",
        evidence_ids=("raw_639e2963e72e4e3bb063042eeb221afd",),
        transcript_paths=(str(transcript_path),),
    )

    result = build_decision_threads(timeline, extraction_run=run, embedder=HashEmbeddingProvider(dims=64))

    assert result.threads
    assert any("install_service.py" in "\n".join(thread.file_paths) for thread in result.threads)
    assert all(thread.extraction_run_id == run.id for thread in result.threads)
