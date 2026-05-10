from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.evidence.triggers import TriggerDecision
from agent_memory_orchestrator.evidence.window import MAX_QWEN_RECORDS, clean_evidence_window
from agent_memory_orchestrator.session_graph import QwenGraphExtractor


FORBIDDEN_QWEN_TERMS = (
    "raw_",
    "hook_event_name",
    "captureonly",
    "manualsmoke",
    "from __future__",
    "status_porcelain",
    "after_preview",
)


class _FakeQwenClient:
    def __init__(self) -> None:
        self.prompt = ""

    def _generate_json(
        self,
        prompt: str,
        *,
        num_predict: int,
        timeout_seconds: float | None = None,
        schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.prompt = prompt
        return {
            "summary": "Updated evidence window cleaning before Qwen graph extraction.",
            "goal": ["clean raw artifacts before graph extraction"],
            "latest_decision": ["Qwen sees cleaned bounded evidence only."],
            "changed_files": ["src/agent_memory_orchestrator/evidence_window.py"],
            "tests": [],
            "blockers": [],
            "next_step": ["Run focused evidence-window tests."],
            "decisions": ["Qwen sees cleaned bounded evidence only."],
            "fixes": [],
            "bugs": [],
        }


def test_clean_evidence_window_removes_raw_artifacts_and_bounds_records() -> None:
    records = _dirty_records()
    trigger = TriggerDecision(True, "write", "write/edit tool detected", is_write=True)

    cleaned = clean_evidence_window(records, trigger)
    encoded = json.dumps(cleaned, ensure_ascii=False).lower()

    assert 1 <= len(cleaned) <= MAX_QWEN_RECORDS
    assert "session_graph.py" in encoded
    assert "code edit applied" in encoded
    for forbidden in FORBIDDEN_QWEN_TERMS:
        assert forbidden not in encoded


def test_qwen_graph_extractor_receives_clean_bounded_prompt(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    fake = _FakeQwenClient()
    extractor = QwenGraphExtractor(settings)
    extractor.client = fake  # type: ignore[assignment]
    trigger = TriggerDecision(True, "write", "write/edit tool detected", is_write=True)

    delta = extractor.extract(session_id="s1", records=_dirty_records(), trigger=trigger)
    lowered = fake.prompt.lower()

    assert delta.summary == "Updated evidence window cleaning before Qwen graph extraction."
    assert delta.goal == "clean raw artifacts before graph extraction"
    assert delta.latest_decision == "Qwen sees cleaned bounded evidence only."
    assert delta.next_step == "Run focused evidence-window tests."
    assert len(fake.prompt) < 5000
    for forbidden in FORBIDDEN_QWEN_TERMS:
        assert forbidden not in lowered
    assert "src/agent_memory_orchestrator/evidence_window.py" in lowered


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


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "legacy.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="disabled",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=tmp_path / "graph" / "amo.kuzu",
        evidence_dir=tmp_path / "evidence",
    )
