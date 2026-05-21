from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.app import daemon as daemon_module
from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.reasoning_graph.embedding_store import GraphEmbeddingRecord
from agent_memory_orchestrator.reasoning_graph.embedding_store import GraphEmbeddingStore
from agent_memory_orchestrator.reasoning_graph.jobs import V2SessionJobStore
from agent_memory_orchestrator.reasoning_graph.jobs.constants import GRAPH_SCHEMA_VERSION
from agent_memory_orchestrator.reasoning_graph.jobs.constants import PIPELINE_VERSION
from agent_memory_orchestrator.reasoning_graph.jobs.reset import adopt_existing_v2_production_storage
from agent_memory_orchestrator.reasoning_graph.jobs.reset import initialize_fresh_v2_production_storage
from agent_memory_orchestrator.reasoning_graph.jobs.reset import reset_production_v2_storage
from agent_memory_orchestrator.reasoning_graph.jobs.runner import require_complete_v2_reset_marker
from agent_memory_orchestrator.reasoning_graph.jobs.runner import V2SessionJobRunner
from agent_memory_orchestrator.reasoning_graph.stage4_contract import STAGE4_CONTRACT_VERSION
from agent_memory_orchestrator.reasoning_graph.stage4_contract import build_stage4_packet_prompt
from agent_memory_orchestrator.reasoning_graph.stage4_contract import stage4_contract_hash
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


def test_v2_runner_fails_instead_of_completing_empty_graph_when_no_work_packets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = tmp_path / "transcript.jsonl"
    transcript_rows = [
        {
            "type": "response_item",
            "timestamp": "2026-05-21T10:00:00Z",
            "payload": {"type": "message", "role": "user", "content": [{"text": "Explain the installer behavior."}]},
        },
        {
            "type": "response_item",
            "timestamp": "2026-05-21T10:01:00Z",
            "payload": {
                "type": "message",
                "role": "assistant",
                "content": [{"text": "I found the issue and should document the decision, but no git commit was created."}],
            },
        },
    ]
    transcript_path.write_text("".join(json.dumps(row) + "\n" for row in transcript_rows), encoding="utf-8")
    evidence_path = settings.evidence_dir / "2026-05-21.jsonl"
    evidence_path.write_text(
        json.dumps(
            {
                "id": "raw_1",
                "session_id": "s-no-commit",
                "event_name": "session_start",
                "transcript_path": str(transcript_path),
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store = V2SessionJobStore(settings)
    try:
        job = store.enqueue_session(
            session_id="s-no-commit",
            boundary_event_id="raw_boundary",
            source_app="codex",
            repo_path=str(tmp_path),
            source_evidence_day="2026-05-21",
        ).job
        runner = V2SessionJobRunner(settings, job_store=store)

        first = runner.run_next()
        second = runner.run_next()

        assert first["stage"] == "evidence_view"
        assert first["status"] == "pending"
        assert second["stage"] == "work_packets"
        assert second["status"] == "failed"
        assert second["error"] == "no_commit_backed_work_packets"

        updated = store.get_job(job["job_id"])
        assert updated is not None
        assert updated["status"] == "failed"
        assert updated["current_stage"] == "work_packets"
        assert "no_commit_backed_work_packets" in updated["error"]["reason"]
        stages = store.list_stages(job["job_id"])
        assert [stage["stage"] for stage in stages] == ["evidence_view", "work_packets"]
        work_stage = stages[1]
        assert work_stage["status"] == "failed"
        assert work_stage["diagnostics"]["quality"]["packet_count"] == 0
        assert Path(work_stage["diagnostics"]["packet_artifact"]).exists()
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
    with pytest.raises(ValueError, match="clean-graph and --clean-retrieval"):
        reset_production_v2_storage(
            settings,
            backup=True,
            clean_graph=False,
            clean_retrieval=True,
            force_if_daemon_running=True,
        )
    with pytest.raises(ValueError, match="clean-graph and --clean-retrieval"):
        reset_production_v2_storage(
            settings,
            backup=True,
            clean_graph=True,
            clean_retrieval=False,
            force_if_daemon_running=True,
        )

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
    assert marker["cleaned"] == {"graph": True, "retrieval": True, "faiss": True}


def test_v2_fresh_init_marks_empty_new_install_without_reset(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)

    result = initialize_fresh_v2_production_storage(settings)

    assert result["ok"] is True
    assert result["created"] is True
    marker = result["marker"]
    assert marker["fresh_install"] is True
    assert marker["pipeline_version"] == PIPELINE_VERSION
    assert marker["graph_schema_version"] == GRAPH_SCHEMA_VERSION
    assert marker["cleaned"] == {"graph": True, "retrieval": True, "faiss": True}
    assert marker["validated"] == {"graph_empty": True, "retrieval_empty": True}
    assert require_complete_v2_reset_marker(marker) == marker

    again = initialize_fresh_v2_production_storage(settings)
    assert again["created"] is False
    assert again["reason"] == "marker_exists"


def test_v2_fresh_init_refuses_non_empty_retrieval_store(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)
    settings.retrieval_db_path.parent.mkdir(parents=True, exist_ok=True)
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
                    title="existing",
                    body="existing retrieval doc",
                )
            ]
        )
    finally:
        conn.close()

    with pytest.raises(RuntimeError, match="refused_non_empty_stores"):
        initialize_fresh_v2_production_storage(settings)


def test_v2_adopt_production_backs_up_and_preserves_existing_v2_stores(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)
    settings.graph_path.mkdir(parents=True)
    (settings.graph_path / "graph.bin").write_text("existing v2 graph", encoding="utf-8")
    settings.retrieval_db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = connect(settings.retrieval_db_path)
    try:
        RetrievalIndexStore(conn).replace_documents(
            [
                RetrievalDocument(
                    doc_id="doc:1",
                    doc_type="packet",
                    graph_node_id="node:1",
                    node_kind="Packet",
                    packet_id="WP0001",
                    commit_sha="abc123",
                    title="existing v2",
                    body="existing v2 retrieval doc",
                    metadata={"node_metadata": {"graph_schema_version": GRAPH_SCHEMA_VERSION}},
                )
            ]
        )
    finally:
        conn.close()

    with pytest.raises(ValueError):
        adopt_existing_v2_production_storage(
            settings,
            backup=False,
            validate_graph=True,
            validate_retrieval=True,
            force_if_daemon_running=True,
        )
    with pytest.raises(ValueError, match="validate-graph and --validate-retrieval"):
        adopt_existing_v2_production_storage(
            settings,
            backup=True,
            validate_graph=False,
            validate_retrieval=True,
            force_if_daemon_running=True,
        )

    result = adopt_existing_v2_production_storage(
        settings,
        backup=True,
        validate_graph=True,
        validate_retrieval=True,
        force_if_daemon_running=True,
    )

    assert result["ok"] is True
    backup_dir = Path(result["backup_path"])
    assert (backup_dir / "backup_manifest.json").exists()
    assert settings.graph_path.exists()
    assert (settings.graph_path / "graph.bin").read_text(encoding="utf-8") == "existing v2 graph"

    conn = connect(settings.retrieval_db_path)
    try:
        doc_count = conn.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone()[0]
    finally:
        conn.close()
    assert doc_count == 1

    store = V2SessionJobStore(settings)
    try:
        marker = store.marker()
    finally:
        store.close()
    assert marker is not None
    assert marker["adopted_existing_v2"] is True
    assert marker["validated"] == {"graph": True, "retrieval": True}
    assert marker["cleaned"] == {"graph": False, "retrieval": False, "faiss": False}
    assert require_complete_v2_reset_marker(marker) == marker


def test_v2_runner_rejects_missing_incomplete_or_wrong_reset_marker() -> None:
    complete_marker = {
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "cleaned": {"graph": True, "retrieval": True, "faiss": True},
    }
    adopted_marker = {
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "adopted_existing_v2": True,
        "validated": {"graph": True, "retrieval": True},
        "cleaned": {"graph": False, "retrieval": False, "faiss": False},
    }

    assert require_complete_v2_reset_marker(complete_marker) == complete_marker
    assert require_complete_v2_reset_marker(adopted_marker) == adopted_marker
    with pytest.raises(RuntimeError, match="missing"):
        require_complete_v2_reset_marker(None)
    with pytest.raises(RuntimeError, match="version_mismatch"):
        require_complete_v2_reset_marker({**complete_marker, "pipeline_version": "old"})
    with pytest.raises(RuntimeError, match="incomplete"):
        require_complete_v2_reset_marker({**complete_marker, "cleaned": {"graph": True, "retrieval": False}})
    with pytest.raises(RuntimeError, match="incomplete"):
        require_complete_v2_reset_marker({**adopted_marker, "validated": {"graph": True, "retrieval": False}})


def test_stage4_prompt_uses_reset_contract_module() -> None:
    prompt = build_stage4_packet_prompt(
        {
            "packet_id": "WP0001",
            "commit": {"short_sha": "abc1234"},
            "problem_refs": [{"ref": "E00001", "excerpt": "problem"}],
            "rationale_refs": [],
            "validation_refs": [],
        }
    )

    assert STAGE4_CONTRACT_VERSION == "stage4-reset-2026-05-14"
    assert len(stage4_contract_hash()) == 64
    assert "Support refs are provenance only" in prompt
    assert "Input packet:" in prompt


def test_auto_drain_closes_graph_before_v2_runner_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = make_settings(tmp_path)
    events: list[str] = []

    class FakeGraph:
        def __init__(self, settings: Settings) -> None:
            del settings
            events.append("graph_open")

        def drain_evidence(self, **kwargs: object) -> dict[str, object]:
            del kwargs
            events.append("drain")
            return {"records_ingested": 1, "windows_processed": 0, "stopped_reason": "done", "pending_sessions": []}

        def close(self) -> None:
            events.append("graph_close")

    class FakeRunner:
        def __init__(self, settings: Settings) -> None:
            del settings
            events.append("runner_open")

        def run_next(self) -> dict[str, object]:
            events.append("runner_run")
            return {"ok": True, "ran": False}

        def close(self) -> None:
            events.append("runner_close")

    monkeypatch.setattr(daemon_module, "GraphRagService", FakeGraph)
    monkeypatch.setattr(daemon_module, "V2SessionJobRunner", FakeRunner)

    result = daemon_module._run_auto_drain_once(settings)

    assert result["records_ingested"] == 1
    assert events == ["graph_open", "drain", "graph_close", "runner_open", "runner_run", "runner_close"]


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
