from __future__ import annotations

from agent_memory_orchestrator.evidence.triggers import detect_trigger


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


def test_apply_patch_tool_triggers_write_window() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "apply_patch", "content": "Updated src/app.py"},
        }
    )

    assert decision.should_process is True
    assert decision.is_write is True
    assert decision.trigger_type == "write"


def test_test_command_after_pending_write_triggers_test_window() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "shell_command", "content": "python -m pytest -q"},
        },
        pending_write=True,
    )

    assert decision.should_process is True
    assert decision.trigger_type == "test"


def test_finalize_prompt_triggers_explicit_memory_window() -> None:
    decision = detect_trigger(
        {
            "event_name": "user_prompt_submit",
            "payload": {"prompt": "final decision: remember this graph architecture"},
        }
    )

    assert decision.should_process is True
    assert decision.trigger_type == "explicit_finalize"
