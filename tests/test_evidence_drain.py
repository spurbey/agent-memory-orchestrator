from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.evidence.drain import EvidenceDrain
from agent_memory_orchestrator.evidence.drain import _read_jsonl_from
from agent_memory_orchestrator.evidence.raw_store import RawEvidenceStore
from agent_memory_orchestrator.infrastructure.sqlite.production_job_store import ProductionSessionJobStore


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


def test_drain_read_only_prompt_is_evidence_only_and_idempotent(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    RawEvidenceStore(settings.evidence_dir).append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "read docs only"},
        session_id="s1",
        source_app="codex",
        event_name="user_prompt_submit",
    )
    drain = _drain(settings)

    first = drain.drain()
    second = drain.drain()

    assert first["records_ingested"] == 1
    assert first["windows_processed"] == 0
    assert first["pending_sessions"] == 1
    assert second["records_seen"] == 0
    assert _job_by_session(settings, "s1") is None


def test_drain_skips_and_quarantines_malformed_jsonl_lines(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    path = settings.evidence_dir / "2026-05-20.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    valid_1 = {
        "id": "raw_good_1",
        "hash": "h1",
        "session_id": "s1",
        "source_app": "codex",
        "event_name": "user_prompt_submit",
        "created_at": "2026-05-20T00:00:00+00:00",
        "payload": {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "first valid row"},
    }
    valid_2 = {
        "id": "raw_good_2",
        "hash": "h2",
        "session_id": "s1",
        "source_app": "codex",
        "event_name": "user_prompt_submit",
        "created_at": "2026-05-20T00:00:01+00:00",
        "payload": {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "second valid row"},
    }
    path.write_bytes(
        (
            json.dumps(valid_1)
            + "\n"
            + 'broken tool_response fragment", "session_id": "s1"}\n'
            + json.dumps(valid_2)
            + "\n"
        ).encode("utf-8")
    )

    first = _drain(settings).drain()
    second = _drain(settings).drain()

    assert first["records_ingested"] == 2
    assert first["malformed_records"] == 1
    assert first["malformed"][0]["error_type"] == "JSONDecodeError"
    assert first["malformed"][0]["quarantine_path"]
    assert Path(first["malformed"][0]["quarantine_path"]).exists()
    assert "broken tool_response fragment" in Path(first["malformed"][0]["quarantine_path"]).read_text(encoding="utf-8")
    assert second["records_seen"] == 0
    assert second["malformed_records"] == 0
    assert [row["id"] for _, row in _read_jsonl_from(path, 0)] == ["raw_good_1", "raw_good_2"]


def test_drain_session_boundary_persists_pending_window_across_runs(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    evidence = RawEvidenceStore(settings.evidence_dir)
    _append_session_start(evidence, "s1")
    evidence.append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "short context only"},
        session_id="s1",
        source_app="codex",
        event_name="user_prompt_submit",
    )

    first = _drain(settings).drain(limit=2)
    assert first["windows_processed"] == 0
    assert first["pending_sessions"] == 1

    _append_session_start(evidence, "s2")

    second = _drain(settings).drain(limit=1)

    assert second["windows_processed"] == 1
    assert second["triggered"][0]["session_id"] == "s1"
    assert second["triggered"][0]["trigger"]["trigger_type"] == "session_boundary"
    assert second["triggered"][0]["result"]["mode"] == "production_job_enqueue"
    assert second["triggered"][0]["result"]["created"] is True
    assert second["pending_sessions"] == 1
    job = _job_by_session(settings, "s1")
    assert job is not None
    assert job["status"] == "pending"
    assert job["current_stage"] == "evidence_view"
    assert job["source_app"] == "codex"
    assert job["source_first_event_id"]
    assert job["source_latest_event_id"]
    assert job["source_first_event_id"] != job["source_latest_event_id"]


def test_session_filtered_drain_uses_session_cursor_and_matching_limit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    evidence = RawEvidenceStore(settings.evidence_dir)
    for index in range(30):
        evidence.append(
            {"hook_event_name": "UserPromptSubmit", "session_id": f"other-{index}", "prompt": "read only"},
            session_id=f"other-{index}",
            source_app="codex",
            event_name="user_prompt_submit",
        )
    _append_session_start(evidence, "target")
    evidence.append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "target", "prompt": "clean write window"},
        session_id="target",
        source_app="codex",
        event_name="user_prompt_submit",
    )
    evidence.append(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "target",
            "tool": "apply_patch",
            "tool_response": json.dumps(
                {
                    "output": "Success. Updated the following files:\nM C:\\repo\\src\\agent_memory_orchestrator\\evidence_window.py\n",
                    "metadata": {"exit_code": 0},
                }
            ),
        },
        session_id="target",
        source_app="codex",
        event_name="post_tool_use",
    )
    _append_session_start(evidence, "other-after-target")

    result = _drain(settings).drain(session_id="target", limit=10)
    cursors = json.loads((settings.home / ".state" / "evidence_cursors.json").read_text(encoding="utf-8"))

    assert result["records_ingested"] == 3
    assert result["windows_processed"] == 1
    assert result["triggered"][0]["result"]["mode"] == "production_job_enqueue"
    assert _job_by_session(settings, "target") is not None
    assert cursors
    assert all("::session::target" in key for key in cursors)


def test_drain_stops_after_max_windows(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    evidence = RawEvidenceStore(settings.evidence_dir)
    for index in range(2):
        _append_session_start(evidence, f"s{index + 1}")
        evidence.append(
            {"hook_event_name": "UserPromptSubmit", "session_id": f"s{index + 1}", "prompt": f"write window {index}"},
            session_id=f"s{index + 1}",
            source_app="codex",
            event_name="user_prompt_submit",
        )
        evidence.append(
            {
                "hook_event_name": "PostToolUse",
                "session_id": f"s{index + 1}",
                "tool": "apply_patch",
                "tool_response": json.dumps(
                    {
                        "output": f"Success. Updated the following files:\nM C:\\repo\\src\\window_{index}.py\n",
                        "metadata": {"exit_code": 0},
                    }
                ),
            },
            session_id=f"s{index + 1}",
            source_app="codex",
            event_name="post_tool_use",
        )
    _append_session_start(evidence, "s3")

    result = _drain(settings).drain(limit=20, max_windows=1)
    second = _drain(settings).drain(limit=20, max_windows=1)

    assert result["windows_processed"] == 1
    assert result["stopped_reason"] == "max_windows_reached"
    assert result["max_windows"] == 1
    assert second["windows_processed"] == 1
    assert _job_by_session(settings, "s1") is not None
    assert _job_by_session(settings, "s2") is not None


def _append_session_start(evidence: RawEvidenceStore, session_id: str) -> None:
    evidence.append(
        {"hook_event_name": "SessionStart", "session_id": session_id},
        session_id=session_id,
        source_app="codex",
        event_name="session_start",
    )


def _drain(settings: Settings) -> EvidenceDrain:
    return EvidenceDrain(settings)


def _job_by_session(settings: Settings, session_id: str) -> dict | None:
    job_store = ProductionSessionJobStore(settings)
    try:
        return job_store.get_job_by_session(session_id=session_id)
    finally:
        job_store.close()
