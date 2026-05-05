from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_memory_orchestrator.cli import _rebuild_clean_db
from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.memory_service import MemoryService


def make_settings(tmp_path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "agent_memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="BAAI/bge-m3",
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


def test_ingest_and_search(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()

    transcript = tmp_path / "sample.jsonl"
    transcript.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "event_type": "response",
                        "content": "Designed memory retrieval with lexical and vector ranking.",
                    }
                ),
                json.dumps(
                    {
                        "event_type": "tool_result",
                        "content": "Implemented orchestrator transition checks and blocker gates.",
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = svc.ingest_transcript(agent="claude", file_path=transcript, session_id="s1")
    assert result["events"] == 2
    assert result["memories"] == 2
    assert result["chunks"] == 2

    hits = svc.search_memories(query="orchestrator", session_id="s1", limit=5)
    assert len(hits) == 1
    assert "orchestrator" in hits[0]["summary"].lower()
    assert hits[0]["source_ranks"]

    metrics = svc.inspect_metrics()
    assert metrics["counts"]["pipeline_runs"] == 2
    assert metrics["counts"]["retrieval_runs"] == 1
    assert metrics["counts"]["retrieval_candidates"] >= 1

    svc.close()


def test_decision_query_prefers_specific_high_confidence_hook_memory(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()
    svc.create_session("s1", "Session 1")
    generic_event = svc.add_event(
        "s1",
        "codex",
        "response",
        "This changes the product from local coding memory into a broader agent coordination platform.",
    )
    hook_event = svc.add_event(
        "s1",
        "codex",
        "response",
        "Codex hooks are enabled with codex_hooks and use SessionStart and UserPromptSubmit.",
    )
    svc.add_memory_unit(
        session_id="s1",
        source_event_id=generic_event.id,
        source_chunk_id=None,
        memory_type="observation",
        subject="changes",
        predicate="observes",
        object_text="local coding memory becomes broader agent coordination",
        summary=(
            "Observation: This changes the product from local coding memory into a broader "
            "agent coordination platform."
        ),
        topic_key="changes",
        entities=[],
        tags=["agent", "memory", "coordination"],
        confidence=0.4,
        importance=0.45,
    )
    svc.add_memory_unit(
        session_id="s1",
        source_event_id=generic_event.id,
        source_chunk_id=None,
        memory_type="observation",
        subject="/.codex/config.toml",
        predicate="observes",
        object_text="That result means what did we decide about agent memory hooks was answered.",
        summary="Observation [/.codex/config.toml]: That result means what did we decide about agent memory hooks was answered.",
        topic_key="codex_config_toml",
        entities=["/.codex/config.toml"],
        tags=["agent", "memory", "hooks"],
        confidence=0.4,
        importance=0.49,
    )
    hook = svc.add_memory_unit(
        session_id="s1",
        source_event_id=hook_event.id,
        source_chunk_id=None,
        memory_type="fix",
        subject="/.codex/config.toml",
        predicate="fixes",
        object_text="Codex hooks use codex_hooks, SessionStart, UserPromptSubmit, PostToolUse, Stop.",
        summary=(
            "Fix [/.codex/config.toml, SessionStart, UserPromptSubmit]: Codex hooks are "
            "enabled with codex_hooks. Events: SessionStart, UserPromptSubmit, PostToolUse, Stop."
        ),
        topic_key="codex_config_toml",
        entities=["/.codex/config.toml", "SessionStart", "UserPromptSubmit", "PostToolUse", "Stop"],
        tags=["codex", "hooks", "docs"],
        confidence=0.9,
        importance=0.8,
    )

    hits = svc.search_memories("what did we decide about agent memory hooks", session_id="s1", limit=5)
    assert hits[0]["memory_id"] == hook.id
    assert hits[0]["ranking_policy"]["type_boost"] > 0
    assert hits[0]["ranking_policy"]["exact_boost"] > 0
    assert hits[0]["ranking_policy"]["confidence_boost"] > hits[1]["ranking_policy"]["confidence_boost"]

    svc.close()


def test_export_snapshot_with_session_filter(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()
    svc.create_session("s1", "Session 1")
    event = svc.add_event("s1", "claude", "response", "Persisted export filtering behavior.")
    svc.add_memory("s1", event.id, "Persisted export filtering behavior.")

    out_path = tmp_path / "exports" / "snapshot.jsonl"
    rows = svc.export_snapshot(out_path=out_path, session_id="s1")
    assert rows > 0
    assert out_path.exists()
    svc.close()


def test_hook_ingestion_redacts_chunks_extracts_and_summarizes(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()

    payload = {
        "session_id": "hook-s1",
        "hook_event_name": "PostToolUse",
        "tool_name": "shell",
        "content": "Implemented scraper/retry.py retry jitter fix. api_key=secret123",
    }
    result = svc.ingest_hook_payload(payload, default_agent="codex")
    assert result["session_id"] == "hook-s1"

    event = svc.timeline("hook-s1", limit=1)[0]
    assert "***REDACTED***" in event["content"]
    assert event["redacted"] is True

    summary = svc.generate_session_summary("hook-s1")
    assert "scraper/retry.py" in summary["summary_text"]

    svc.close()


def test_ide_context_is_kept_raw_but_cleaned_before_memory_extraction(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()

    raw_prompt = """# Context from my IDE setup:

## Active file: Untitled9.md
## Open tabs:
- Untitled9.md: Untitled9.md
- temp_result.txt: temp_result.txt

## My request for Codex:
final decision: use Codex hooks through UserPromptSubmit for memory retrieval.
"""
    event = svc.add_event("s1", "user", "prompt", raw_prompt, process=True)
    raw_event = svc.timeline("s1", limit=1)[0]
    assert "Open tabs" in raw_event["content"]

    chunk = svc.conn.execute(
        "SELECT text, metadata_json FROM chunks WHERE event_id = ?",
        (event.id,),
    ).fetchone()
    assert chunk is not None
    assert "Open tabs" not in chunk["text"]
    assert "final decision" in chunk["text"]
    metadata = json.loads(chunk["metadata_json"])
    assert metadata["amo_cleaning"]["removed_ide_context"] is True
    assert metadata["amo_promote_memory"] is True

    memories = svc.search_memories("Codex hooks UserPromptSubmit", session_id="s1", limit=5)
    assert memories
    assert memories[0]["memory_type"] == "decision"
    svc.close()


def test_low_value_tool_output_creates_evidence_chunk_but_no_memory_unit(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()

    event = svc.add_event(
        "s1",
        "codex",
        "tool_result",
        'Command completed: ["powershell.exe", "-Command", "rg \\"^# Cell\\" Untitled9.md"]\nOutput:\n# Cell 1\n# Cell 2',
        process=True,
    )

    chunk = svc.conn.execute(
        "SELECT metadata_json FROM chunks WHERE event_id = ?",
        (event.id,),
    ).fetchone()
    assert chunk is not None
    metadata = json.loads(chunk["metadata_json"])
    assert metadata["amo_promote_memory"] is False
    assert metadata["amo_suppression_reason"] == "low_value_tool_output"

    memories = svc.conn.execute(
        "SELECT COUNT(*) FROM memory_units WHERE source_event_id = ?",
        (event.id,),
    ).fetchone()[0]
    assert memories == 0

    pipeline = svc.conn.execute(
        "SELECT metrics_json FROM pipeline_runs WHERE source_event_id = ?",
        (event.id,),
    ).fetchone()
    metrics = json.loads(pipeline["metrics_json"])
    assert metrics["suppressed_memory_chunks"] == 1
    assert metrics["cleanup_reasons"]["low_value_tool_output"] == 1
    svc.close()


def test_consolidation_supersedes_and_historical_search(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()
    svc.create_session("s1", "Session 1")
    event1 = svc.add_event("s1", "claude", "response", "Implemented retry.py fixed delay retry logic.")
    event2 = svc.add_event("s1", "codex", "response", "Implemented retry.py exponential backoff retry logic.")
    first = svc.add_memory_unit(
        session_id="s1",
        source_event_id=event1.id,
        source_chunk_id=None,
        memory_type="fix",
        subject="retry.py",
        predicate="fixes",
        object_text="fixed delay retry logic",
        summary="Fix [retry.py]: fixed delay retry logic",
        topic_key="retry_py",
        entities=["retry.py"],
        tags=["retry", "logic"],
        confidence=0.7,
    )
    second = svc.add_memory_unit(
        session_id="s1",
        source_event_id=event2.id,
        source_chunk_id=None,
        memory_type="fix",
        subject="retry.py",
        predicate="fixes",
        object_text="exponential backoff retry logic",
        summary="Fix [retry.py]: exponential backoff retry logic",
        topic_key="retry_py",
        entities=["retry.py"],
        tags=["retry", "logic"],
        confidence=0.9,
    )
    assert first.id != second.id

    active_hits = svc.search_memories("retry logic", session_id="s1", limit=10)
    assert all(hit["status"] == "active" for hit in active_hits)

    historical_hits = svc.search_memories("fixed delay retry logic", session_id="s1", limit=10, include_historical=True)
    assert any(hit["status"] == "superseded" for hit in historical_hits)

    decision = svc.conn.execute(
        "SELECT relation, score_breakdown_json FROM consolidation_decisions WHERE new_memory_id = ? ORDER BY created_at DESC LIMIT 1",
        (second.id,),
    ).fetchone()
    assert decision["relation"] == "supersedes"
    assert "cosine" in decision["score_breakdown_json"]
    svc.close()


def test_dashboard_snapshot_and_retrieval_detail(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()

    transcript = tmp_path / "sample.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "event_type": "response",
                "content": "Implemented scraper/retry.py exponential backoff with jitter after critique.",
            }
        ),
        encoding="utf-8",
    )
    svc.ingest_transcript(agent="codex", file_path=transcript, session_id="dash")
    hits = svc.search_memories("why retry jitter", session_id="dash", limit=5)
    assert hits
    assert hits[0]["reranker_backend"] == "lexical"
    assert hits[0]["reranker_model"] == "lexical_overlap_v1"

    snapshot = svc.dashboard_snapshot(limit=10)
    assert snapshot["metrics"]["counts"]["retrieval_runs"] == 1
    assert snapshot["sessions"][0]["id"] == "dash"
    assert snapshot["recent_events"]
    assert snapshot["recent_memories"]
    assert snapshot["retrieval_runs"][0]["query"] == "why retry jitter"

    detail = svc.retrieval_run_detail(snapshot["retrieval_runs"][0]["id"])
    assert detail["run"]["query"] == "why retry jitter"
    assert detail["run"]["config"]["actual_reranker"]["backend"] == "lexical"
    assert detail["candidates"][0]["memory_id"] == hits[0]["memory_id"]
    assert detail["candidates"][0]["score_breakdown"]["reranker"]["backend"] == "lexical"

    svc.close()


def test_codex_rollout_import_normalizes_useful_events(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()

    root = tmp_path / "codex" / "sessions"
    root.mkdir(parents=True)
    rollout = root / "rollout-test.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-05T00:00:00Z",
                        "type": "session_meta",
                        "payload": {
                            "id": "codex-session-1",
                            "cwd": str(tmp_path),
                            "source": "vscode",
                            "model_provider": "openai",
                            "base_instructions": {"text": "large skipped instructions"},
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-05T00:00:01Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "Why did retry jitter change?",
                            "turn_id": "turn-1",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-05T00:00:02Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": "Implemented scraper/retry.py exponential backoff with jitter.",
                            "turn_id": "turn-1",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-05T00:00:03Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "info": None,
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = svc.import_codex_sessions(root, limit=10)
    assert result["totals"]["files"] == 1
    assert result["totals"]["events"] == 3
    assert result["totals"]["memory_units"] >= 1
    hits = svc.search_memories("retry jitter", session_id="codex-session-1")
    assert hits
    assert any("scraper/retry.py" in hit["summary"] for hit in hits)
    svc.close()


def test_codex_user_prompt_hook_returns_additional_context_when_auto_safe(tmp_path) -> None:
    settings = replace(make_settings(tmp_path), approval_mode="auto_safe")
    svc = MemoryService(settings)
    svc.init_db()
    svc.create_session("prior", "Prior")
    event = svc.add_event(
        "prior",
        "codex",
        "response",
        "Implemented scraper/retry.py exponential backoff with jitter because fixed delay caused rate limits.",
    )
    svc.add_memory_unit(
        session_id="prior",
        source_event_id=event.id,
        source_chunk_id=None,
        memory_type="decision",
        subject="scraper/retry.py",
        predicate="decides",
        object_text="retry logic uses exponential backoff with jitter",
        summary="Decision [scraper/retry.py]: retry logic uses exponential backoff with jitter.",
        topic_key="scraper_retry_py",
        entities=["scraper/retry.py"],
        tags=["retry", "logic", "jitter"],
        confidence=0.9,
        importance=0.8,
    )

    response = svc.codex_hook_response(
        {
            "session_id": "new-session",
            "hook_event_name": "UserPromptSubmit",
            "turn_id": "turn-1",
            "prompt": "Why does retry logic use jitter?",
        },
        default_agent="codex",
    )

    assert response["continue"] is True
    output = response["hookSpecificOutput"]
    assert output["hookEventName"] == "UserPromptSubmit"
    assert "additionalContext" in output
    assert "memory_id=" in output["additionalContext"]
    assert "retry logic" in output["additionalContext"]
    svc.close()


def test_context_pack_prioritizes_durable_memory_and_tracks_exclusions(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()
    svc.create_session("s1", "Session 1")
    decision_event = svc.add_event(
        "s1",
        "codex",
        "response",
        "Decision: scraper retry logic uses exponential backoff with jitter.",
    )
    weak_event = svc.add_event(
        "s1",
        "codex",
        "response",
        "General observation that retry logic and jitter were discussed.",
    )
    decision = svc.add_memory_unit(
        session_id="s1",
        source_event_id=decision_event.id,
        source_chunk_id=None,
        memory_type="decision",
        subject="scraper/retry.py",
        predicate="decides",
        object_text="retry logic uses exponential backoff with jitter",
        summary="Decision [scraper/retry.py]: retry logic uses exponential backoff with jitter.",
        topic_key="scraper_retry_py",
        entities=["scraper/retry.py"],
        tags=["retry", "jitter", "backoff"],
        confidence=0.95,
        importance=0.9,
    )
    weak = svc.add_memory_unit(
        session_id="s1",
        source_event_id=weak_event.id,
        source_chunk_id=None,
        memory_type="observation",
        subject="retry logic",
        predicate="observes",
        object_text="retry logic and jitter were discussed",
        summary="Observation: retry logic and jitter were discussed vaguely.",
        topic_key="retry_logic",
        entities=[],
        tags=["retry", "logic", "jitter"],
        confidence=0.4,
        importance=0.4,
    )

    pack = svc.build_context_pack("why does retry logic use jitter", session_id="s1", budget_tokens=600, limit=10)
    assert pack["text"].startswith("AMO local memory context")
    assert pack["items"]
    assert pack["items"][0]["memory_id"] == decision.id
    assert pack["items"][0]["source_event_id"] == decision_event.id
    assert "high confidence" in pack["items"][0]["include_reason"]
    assert any(item["memory_id"] == weak.id and item["reason"] == "observation_noise" for item in pack["excluded"])
    svc.close()


def test_rebuild_indexes_force_vectors_records_metadata(tmp_path) -> None:
    settings = make_settings(tmp_path)
    svc = MemoryService(settings)
    svc.init_db()
    svc.create_session("s1", "Session 1")
    event = svc.add_event("s1", "codex", "response", "Decision: retry logic uses jitter.")
    svc.add_memory_unit(
        session_id="s1",
        source_event_id=event.id,
        source_chunk_id=None,
        memory_type="decision",
        subject="retry.py",
        predicate="decides",
        object_text="retry logic uses jitter",
        summary="Decision [retry.py]: retry logic uses jitter.",
        topic_key="retry_py",
        entities=["retry.py"],
        tags=["retry", "jitter"],
        confidence=0.95,
    )

    result = svc.rebuild_indexes(force_vectors=True)
    assert result["memory_units"] == 1
    assert result["vectors_written"] == 1
    assert result["faiss"]["status"] == "disabled"

    row = svc.conn.execute("SELECT metadata_json FROM index_versions ORDER BY created_at DESC LIMIT 1").fetchone()
    metadata = json.loads(row["metadata_json"])
    assert metadata["force_vectors"] is True
    assert metadata["vectors_written"] == 1
    svc.close()


def test_rebuild_clean_db_refuses_overwrite_and_rebuilds_from_codex_rollout(tmp_path) -> None:
    settings = make_settings(tmp_path)
    out = tmp_path / "clean.db"
    out.write_text("existing", encoding="utf-8")
    root = tmp_path / "codex" / "sessions"
    root.mkdir(parents=True)

    with pytest.raises(FileExistsError):
        _rebuild_clean_db(settings, out, root, limit=10, force=False)

    rollout = root / "rollout-test.jsonl"
    rollout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "timestamp": "2026-05-05T00:00:00Z",
                        "type": "session_meta",
                        "payload": {"id": "clean-session", "cwd": str(tmp_path), "source": "vscode"},
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-05-05T00:00:01Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "message": (
                                "Implemented .codex/config.toml hook retrieval with "
                                "Codex UserPromptSubmit."
                            ),
                            "turn_id": "turn-1",
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = _rebuild_clean_db(settings, out, root, limit=10, force=True)
    assert result["out"] == str(out.resolve())
    assert result["import"]["totals"]["files"] == 1
    assert result["indexes"]["memory_units"] >= 1

    clean_settings = replace(settings, db_path=out.resolve())
    svc = MemoryService(clean_settings)
    try:
        hits = svc.search_memories("Codex hooks UserPromptSubmit", session_id="clean-session", limit=5)
        assert hits
    finally:
        svc.close()
