from __future__ import annotations

from agent_memory_orchestrator.domain.reasoning import DecisionThread
from agent_memory_orchestrator.domain.reasoning import ExtractionRun
from agent_memory_orchestrator.domain.reasoning import TimelineEvent
from agent_memory_orchestrator.domain.reasoning import extract_decisions


class _QwenDecisionStub:
    def __init__(self, payload):
        self.payload = payload

    def extract(self, payload):
        self.last_input = payload
        return self.payload


def _event(event_id: str, event_type: str, content: str, *, tool_name: str = "") -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        session_id="s1",
        event_type=event_type,
        timestamp="2026-05-14T00:00:00.000Z",
        content=content,
        tool_name=tool_name,
    )


def _thread(*event_ids: str) -> DecisionThread:
    return DecisionThread(
        id="thread:s1:repo-scaffold",
        session_id="s1",
        extraction_run_id="run1",
        event_ids=tuple(event_ids),
        topic="repo scaffold",
        file_paths=("src/agent_memory_orchestrator/config.py",),
        evidence_ids=("raw1",),
    )


def _run() -> ExtractionRun:
    return ExtractionRun.create(session_id="s1", evidence_ids=("raw1",), run_id="run1")


def test_qwen_duplicate_decision_becomes_review_candidate() -> None:
    event = _event("e1", "tool_result", "pytest passed")
    qwen = _QwenDecisionStub(
        {
            "decisions": [
                {
                    "decision_type": "completed_fix",
                    "subject": "hook launcher",
                    "predicate": "handles",
                    "object": "SystemExit",
                    "reason": "capture should fail open",
                    "confidence": 0.9,
                    "evidence_event_ids": ["e1"],
                },
                {
                    "decision_type": "completed_fix",
                    "subject": "hook launcher",
                    "predicate": "handles",
                    "object": "SystemExit",
                    "reason": "capture should fail open",
                    "confidence": 0.91,
                    "evidence_event_ids": ["e1"],
                },
            ]
        }
    )

    result = extract_decisions(thread=_thread("e1"), events=[event], extraction_run=_run(), qwen=qwen)

    assert len(result.decisions) == 1
    assert result.review_candidates[0]["reason"] == "duplicate_decision"


def test_qwen_planned_action_from_write_patch_is_review_only() -> None:
    event = _event("e1", "tool_use", "*** Begin Patch\n*** Add File: config.py", tool_name="apply_patch")
    qwen = _QwenDecisionStub(
        {
            "decisions": [
                {
                    "decision_type": "planned_action",
                    "subject": "write_patch",
                    "predicate": "write",
                    "object": "config.py",
                    "reason": "The assistant planned to add configuration code.",
                    "confidence": 0.9,
                    "evidence_event_ids": ["e1"],
                }
            ]
        }
    )

    result = extract_decisions(thread=_thread("e1"), events=[event], extraction_run=_run(), qwen=qwen)

    assert result.decisions == ()
    assert result.review_candidates[0]["reason"] == "planned_action_tool_only_evidence"


def test_qwen_planned_action_with_user_and_assistant_evidence_is_accepted() -> None:
    user = _event("e1", "user_message", "make a new repo under dora")
    assistant = _event("e2", "agent_message", "I will create a standalone repo under Dora.")
    qwen = _QwenDecisionStub(
        {
            "decisions": [
                {
                    "decision_type": "planned_action",
                    "subject": "repository layout",
                    "predicate": "create",
                    "object": "Dora/agent-memory-orchestrator",
                    "reason": "The user wanted a standalone repo under Dora.",
                    "confidence": 0.9,
                    "evidence_event_ids": ["e1", "e2"],
                }
            ]
        }
    )

    result = extract_decisions(thread=_thread("e1", "e2"), events=[user, assistant], extraction_run=_run(), qwen=qwen)

    assert len(result.decisions) == 1
    assert result.decisions[0].metadata["decision_type"] == "planned_action"
    assert result.review_candidates == ()
