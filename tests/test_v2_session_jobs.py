from __future__ import annotations

from pathlib import Path

import pytest

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.reasoning_graph.embedding_store import GraphEmbeddingRecord
from agent_memory_orchestrator.reasoning_graph.embedding_store import GraphEmbeddingStore
from agent_memory_orchestrator.reasoning_graph.jobs import V2SessionJobStore
from agent_memory_orchestrator.reasoning_graph.jobs.constants import GRAPH_SCHEMA_VERSION
from agent_memory_orchestrator.reasoning_graph.jobs.constants import PIPELINE_VERSION
from agent_memory_orchestrator.reasoning_graph.jobs.reset import reset_production_v2_storage
from agent_memory_orchestrator.reasoning_graph.retrieval import RetrievalDocument
from agent_memory_orchestrator.reasoning_graph.retrieval import RetrievalIndexStore
from agent_memory_orchestrator.graph.service import GraphRagService
from agent_memory_orchestrator.graph.store import InMemoryGraphStore


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / ".data" / "main.sqlite",
        retrieval_db_path=tmp_path / ".data" / "retrieval.sqlite",
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
        graph_path=tmp_path / ".graph" / "amo.kuzu",
        evidence_dir=tmp_path / ".evidence",
    )


def test_v2_enqueue_is_idempotent_and_atomic_lock_skips_locked_failed_and_pending_model(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = V2SessionJobStore(settings)
    try:
        first = store.enqueue_session(
            session_id="s1",
            boundary_event_id="raw_boundary_1",
            source_app="codex",
            repo_path=str(tmp_path),
            source_evidence_day="2026-05-20",
        )
        second = store.enqueue_session(
            session_id="s1",
            boundary_event_id="raw_boundary_1",
            source_app="codex",
            repo_path=str(tmp_path),
            source_evidence_day="2026-05-20",
        )

        assert first.created is True
        assert second.created is False
        assert second.reason == "already_enqueued"
        assert second.job["job_id"] == first.job["job_id"]

        acquired = store.acquire_next(owner="runner-a", lease_seconds=60)
        assert acquired is not None
        assert acquired["session_id"] == "s1"
        assert acquired["status"] == "running"
        assert acquired["attempt_count"] == 1
        assert store.acquire_next(owner="runner-b", lease_seconds=60) is None

        store.fail_stage(job_id=acquired["job_id"], stage="evidence_view", reason="boom")
        assert store.acquire_next(owner="runner-c", lease_seconds=60) is None

        retried = store.retry_job(acquired["job_id"], forced_by="test")
        assert retried["status"] == "pending"
        assert retried["forced_by"] == "test"

        reacquired = store.acquire_next(owner="runner-d", lease_seconds=60)
        assert reacquired is not None
        store.set_pending_model(job_id=reacquired["job_id"], stage="qwen_reasoning", reason="qwen_unavailable")
        assert store.acquire_next(owner="runner-e", lease_seconds=60) is None
    finally:
        store.close()


def test_v2_stage_rows_track_hashes_and_config_hash(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = V2SessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s1", boundary_event_id="raw_boundary").job
        store.start_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            input_artifact="in.jsonl",
            input_hash="input-hash",
            stage_config_hash="config-hash",
        )
        store.complete_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            output_artifact="out.json",
            output_hash="output-hash",
            diagnostics={"ok": True},
        )
        stage = store.stage_row(job_id=job["job_id"], stage="evidence_view")
        updated = store.get_job(job["job_id"])

        assert stage is not None
        assert stage["status"] == "complete"
        assert stage["input_hash"] == "input-hash"
        assert stage["output_hash"] == "output-hash"
        assert stage["stage_config_hash"] == "config-hash"
        assert stage["diagnostics"] == {"ok": True}
        assert updated is not None
        assert updated["status"] == "pending"
        assert updated["current_stage"] == "work_packets"
        assert updated["last_successful_stage"] == "evidence_view"
    finally:
        store.close()


def test_v2_reset_requires_backup_and_preserves_raw_config_and_job_tables(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.evidence_dir.mkdir(parents=True)
    raw_path = settings.evidence_dir / "2026-05-20.jsonl"
    raw_path.write_text('{"id":"raw_1","session_id":"s1"}\n', encoding="utf-8")
    settings.home.mkdir(parents=True, exist_ok=True)
    config_path = settings.home / "config.json"
    config_path.write_text('{"local_only": true}\n', encoding="utf-8")
    settings.graph_path.mkdir(parents=True)
    (settings.graph_path / "graph.bin").write_text("legacy graph", encoding="utf-8")
    faiss_dir = settings.retrieval_db_path.parent / "indexes" / settings.retrieval_db_path.stem
    faiss_dir.mkdir(parents=True)
    (faiss_dir / "index.faiss").write_text("legacy faiss", encoding="utf-8")

    conn = connect(settings.retrieval_db_path)
    try:
        RetrievalIndexStore(conn).replace_documents(
            [
                RetrievalDocument(
                    doc_id="doc:1",
                    doc_type="packet",
                    graph_node_id="node:1",
                    node_kind="Packet",
                    packet_id="packet:1",
                    commit_sha="abc123",
                    title="legacy",
                    body="legacy retrieval doc",
                )
            ]
        )
        GraphEmbeddingStore(conn, db_path=settings.retrieval_db_path).upsert(
            GraphEmbeddingRecord.create(
                node_id="node:1",
                node_kind="Packet",
                memory_class="graph_context",
                graph_scope="default",
                graph_path=str(settings.graph_path),
                session_id="s1",
                extraction_run_id="run",
                embedding_kind="retrieval_text",
                model="hash-fallback",
                text="legacy retrieval doc",
                vector=[0.1, 0.2],
            )
        )
    finally:
        conn.close()

    store = V2SessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s1", boundary_event_id="raw_boundary").job
    finally:
        store.close()

    with pytest.raises(ValueError):
        reset_production_v2_storage(settings, backup=False, clean_graph=True, clean_retrieval=True)

    result = reset_production_v2_storage(
        settings,
        backup=True,
        clean_graph=True,
        clean_retrieval=True,
        force_if_daemon_running=True,
    )

    assert result["ok"] is True
    backup_dir = Path(result["backup_path"])
    assert (backup_dir / "backup_manifest.json").exists()
    assert (backup_dir / "config.json").exists()
    assert raw_path.exists()
    assert config_path.exists()
    assert not settings.graph_path.exists()
    assert not faiss_dir.exists()

    conn = connect(settings.retrieval_db_path)
    try:
        doc_count = conn.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone()[0]
        embedding_count = conn.execute("SELECT COUNT(*) FROM graph_embeddings").fetchone()[0]
    finally:
        conn.close()
    assert doc_count == 0
    assert embedding_count == 0

    store = V2SessionJobStore(settings)
    try:
        assert store.get_job(job["job_id"]) is not None
        marker = store.marker()
    finally:
        store.close()
    assert marker is not None
    assert marker["pipeline_version"] == PIPELINE_VERSION
    assert marker["graph_schema_version"] == GRAPH_SCHEMA_VERSION


def test_legacy_graphdelta_smoke_uses_disposable_graph_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    created_paths: list[Path] = []

    class FakeSmokeStore:
        def __init__(self, path: Path) -> None:
            self.path = path
            created_paths.append(path)

        def init_schema(self) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_drain_fresh_graph(self: GraphRagService, store: FakeSmokeStore, **kwargs: object) -> dict[str, object]:
        return {"records_seen": 1, "windows_processed": 1, "cursor_path": str(kwargs["cursor_path"])}

    monkeypatch.setattr("agent_memory_orchestrator.graph.service.KuzuGraphStore", FakeSmokeStore)
    monkeypatch.setattr(GraphRagService, "_drain_fresh_graph", fake_drain_fresh_graph)

    svc = GraphRagService(settings, store=InMemoryGraphStore())
    try:
        result = svc.drain_evidence_smoke(limit=10, max_windows=1)
    finally:
        svc.close()

    assert result["mode"] == "legacy_graphdelta_smoke"
    assert result["graph_path"] != str(settings.graph_path)
    assert ".state" in str(result["graph_path"])
    assert created_paths == [Path(str(result["graph_path"]))]
