from __future__ import annotations

import json

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
        consensus_threshold=0.7,
        max_review_rounds=5,
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

    hits = svc.search_memories(query="orchestrator", session_id="s1", limit=5)
    assert len(hits) == 1
    assert "orchestrator" in hits[0]["summary"].lower()

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
