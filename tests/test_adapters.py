from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.integrations.adapters import infer_codex_session, normalize_adapter_event
from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.memory_service import MemoryService


def test_legacy_adapters_package_keeps_public_exports() -> None:
    from agent_memory_orchestrator import adapters as legacy_adapters

    assert legacy_adapters.normalize_adapter_event is normalize_adapter_event
    assert legacy_adapters.infer_codex_session is infer_codex_session


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "agent_memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="sqlite",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        context_budget=2500,
        reranker_backend="lexical",
        rerank_top_k=50,
        rerank_max_chars=1800,
    )


def test_codex_adapter_normalizes_rollout_event_msg_and_session_meta(tmp_path) -> None:
    session_file = tmp_path / "codex.jsonl"
    session_file.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "payload": {"id": "codex-s1", "cwd": str(tmp_path), "source": "vscode"},
            }
        ),
        encoding="utf-8",
    )
    assert infer_codex_session(session_file)[0] == "codex-s1"

    normalized = normalize_adapter_event(
        {
            "timestamp": "2026-05-07T00:00:00Z",
            "type": "event_msg",
            "payload": {
                "type": "agent_message",
                "message": "Implemented Codex hook adapter normalization.",
                "turn_id": "turn-1",
            },
        },
        default_agent="codex",
        default_session_id="codex-s1",
    )
    assert normalized is not None
    assert normalized["session_id"] == "codex-s1"
    assert normalized["agent"] == "codex"
    assert normalized["event_type"] == "response"
    assert normalized["source_app"] == "codex"
    assert normalized["metadata"]["adapter"] == "codex"
    assert normalized["metadata"]["turn_id"] == "turn-1"


def test_claude_adapter_normalizes_hook_and_message_shapes() -> None:
    hook = normalize_adapter_event(
        {
            "session_id": "claude-s1",
            "hook_event_name": "PostToolUse",
            "tool_name": "Edit",
            "content": "Updated memory_service.py to use adapters.",
            "agent": "claude",
        },
        default_agent="claude",
    )
    assert hook is not None
    assert hook["session_id"] == "claude-s1"
    assert hook["agent"] == "claude"
    assert hook["event_type"] == "post_tool_use"
    assert hook["metadata"]["tool_name"] == "Edit"
    assert hook["metadata"]["adapter"] == "claude"

    message = normalize_adapter_event(
        {
            "sessionId": "claude-s2",
            "type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": "Designed adapter layer."}]},
            "source_app": "claude",
        },
        default_agent="claude",
    )
    assert message is not None
    assert message["agent"] == "claude"
    assert message["event_type"] == "response"
    assert "Designed adapter layer" in message["content"]


def test_omnara_adapter_is_non_authoritative_visibility_input() -> None:
    normalized = normalize_adapter_event(
        {
            "source": "omnara",
            "task_id": "omnara-task-1",
            "actor": "claude",
            "status": "needs_review",
            "summary": "Claude is waiting for Codex review.",
            "id": "external-1",
        },
        default_agent="system",
    )
    assert normalized is not None
    assert normalized["session_id"] == "omnara-task-1"
    assert normalized["source_app"] == "omnara"
    assert normalized["metadata"]["authoritative"] is False
    assert normalized["metadata"]["external_id"] == "external-1"


def test_adapter_ingestion_still_redacts_before_persistence(tmp_path) -> None:
    svc = MemoryService(make_settings(tmp_path))
    try:
        svc.init_db()
        payload = {
            "session_id": "claude-redact",
            "hook_event_name": "UserPromptSubmit",
            "content": "remember this adapter decision. api_key=secret123",
            "agent": "claude",
        }
        normalized = svc.normalize_event_payload(payload, default_agent="claude")
        assert normalized is not None
        event = svc.add_event(
            session_id=str(normalized["session_id"]),
            agent=str(normalized["agent"]),
            event_type=str(normalized["event_type"]),
            content=str(normalized["content"]),
            metadata=dict(normalized["metadata"]),
            source_app=str(normalized["source_app"]),
            process=True,
        )
        assert event.redacted is True
        assert "***REDACTED***" in event.content
        row = svc.conn.execute(
            "SELECT source_app, redacted FROM events WHERE id = ?",
            (event.id,),
        ).fetchone()
        assert row["source_app"] == "claude"
        assert row["redacted"] == 1
    finally:
        svc.close()
