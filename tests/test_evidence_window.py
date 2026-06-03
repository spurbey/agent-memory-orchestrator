from __future__ import annotations

import json
from typing import Any

from agent_memory_orchestrator.evidence.triggers import TriggerDecision
from agent_memory_orchestrator.evidence.window import MAX_QWEN_RECORDS, clean_evidence_window


FORBIDDEN_QWEN_TERMS = (
    "raw_",
    "hook_event_name",
    "captureonly",
    "manualsmoke",
    "from __future__",
    "status_porcelain",
    "after_preview",
)


def test_clean_evidence_window_removes_raw_artifacts_and_bounds_records() -> None:
    records = _dirty_records()
    trigger = TriggerDecision(True, "session_boundary", "new session s2 started after session s1")

    cleaned = clean_evidence_window(records, trigger)
    encoded = json.dumps(cleaned, ensure_ascii=False).lower()

    assert 1 <= len(cleaned) <= MAX_QWEN_RECORDS
    assert "session_graph.py" in encoded
    assert "code edit applied" in encoded
    for forbidden in FORBIDDEN_QWEN_TERMS:
        assert forbidden not in encoded


def _dirty_records() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = [
        {
            "id": "raw_prompt",
            "session_id": "s1",
            "source_app": "codex",
            "event_name": "user_prompt_submit",
            "payload": {
                "hook_event_name": "UserPromptSubmit",
                "prompt": "clean raw artifacts before graph extraction",
            },
        },
        {
            "id": "raw_capture",
            "session_id": "s1",
            "source_app": "codex",
            "event_name": "user_prompt_submit",
            "payload": {
                "continue": True,
                "manualSmoke": False,
                "captureOnly": True,
                "hook_event_name": "UserPromptSubmit",
                "after_preview": "{\"status_porcelain\":\"M noisy.py\"}",
                "evidence": {"id": "raw_abc123456789", "path": r"C:\evidence\2026-05-08.jsonl"},
            },
        },
    ]
    for index in range(14):
        records.append(
            {
                "id": f"raw_read_{index}",
                "session_id": "s1",
                "source_app": "codex",
                "event_name": "post_tool_use",
                "payload": {
                    "hook_event_name": "PostToolUse",
                    "tool": "shell_command",
                    "tool_input": {"command": f"Get-Content src/noisy_{index}.py"},
                    "content": "from __future__ import annotations\nclass Noise:\n    pass",
                },
            }
        )
    records.append(
        {
            "id": "raw_patch",
            "session_id": "s1",
            "source_app": "codex",
            "event_name": "post_tool_use",
            "payload": {
                "hook_event_name": "PostToolUse",
                "tool": "apply_patch",
                "tool_response": json.dumps(
                    {
                        "output": (
                            "Success. Updated the following files:\n"
                            "M C:\\repo\\src\\agent_memory_orchestrator\\session_graph.py\n"
                            "M C:\\repo\\src\\agent_memory_orchestrator\\evidence_window.py\n"
                        ),
                        "metadata": {"exit_code": 0},
                    }
                ),
            },
        }
    )
    return records
