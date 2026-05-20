from __future__ import annotations

from agent_memory_orchestrator.evidence.triggers import detect_trigger
from agent_memory_orchestrator.evidence.triggers import is_session_start
from agent_memory_orchestrator.evidence.triggers import session_boundary_trigger


def test_read_only_prompt_does_not_trigger_graph_build() -> None:
    decision = detect_trigger(
        {
            "event_name": "user_prompt_submit",
            "payload": {"prompt": "what did we decide about codex hooks"},
        }
    )

    assert decision.should_process is False
    assert decision.is_write is False
    assert decision.trigger_type == "none"


def test_apply_patch_tool_does_not_trigger_processing() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "apply_patch", "content": "Updated src/app.py"},
        }
    )

    assert decision.should_process is False
    assert decision.is_write is False
    assert decision.trigger_type == "none"


def test_test_command_does_not_trigger_processing() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "shell_command", "content": "python -m pytest -q"},
        }
    )

    assert decision.should_process is False
    assert decision.trigger_type == "none"


def test_finalize_prompt_does_not_trigger_processing() -> None:
    decision = detect_trigger(
        {
            "event_name": "user_prompt_submit",
            "payload": {"prompt": "final decision: remember this graph architecture"},
        }
    )

    assert decision.should_process is False
    assert decision.trigger_type == "none"


def test_session_start_detection_accepts_codex_event_name_shape() -> None:
    assert is_session_start({"event_name": "session_start"})
    assert is_session_start({"payload": {"hook_event_name": "SessionStart"}})


def test_session_boundary_is_the_processing_trigger() -> None:
    decision = session_boundary_trigger("s1", "s2")

    assert decision.should_process is True
    assert decision.trigger_type == "session_boundary"
    assert "s1" in decision.reason
    assert "s2" in decision.reason
