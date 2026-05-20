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


def test_apply_patch_tool_does_not_trigger_without_token_threshold() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "apply_patch", "content": "Updated src/app.py"},
        }
    )

    assert decision.should_process is False
    assert decision.is_write is False
    assert decision.trigger_type == "none"


def test_test_command_does_not_trigger_without_token_threshold() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "shell_command", "content": "python -m pytest -q"},
        }
    )

    assert decision.should_process is False
    assert decision.trigger_type == "none"


def test_finalize_prompt_does_not_trigger_without_token_threshold() -> None:
    decision = detect_trigger(
        {
            "event_name": "user_prompt_submit",
            "payload": {"prompt": "final decision: remember this graph architecture"},
        }
    )

    assert decision.should_process is False
    assert decision.trigger_type == "none"


def test_pending_token_threshold_is_the_only_processing_trigger() -> None:
    decision = detect_trigger(
        {
            "event_name": "post_tool_use",
            "payload": {"tool": "apply_patch", "content": "Updated src/app.py"},
        },
        pending_approx_tokens=100,
        token_threshold=100,
    )

    assert decision.should_process is True
    assert decision.trigger_type == "token_threshold"
    assert decision.approx_tokens == 100
