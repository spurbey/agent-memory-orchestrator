from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import MethodType

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.reasoning_graph.central_merge.production_eval import run_production_semantic_eval
from agent_memory_orchestrator.reasoning_graph.jobs import V2SessionJobStore
from agent_memory_orchestrator.reasoning_graph.jobs.runner import StageResult
from agent_memory_orchestrator.reasoning_graph.jobs.runner import V2SessionJobRunner
from agent_memory_orchestrator.reasoning_graph.jobs.runner import stage_config_hash
from agent_memory_orchestrator.reasoning_graph.jobs.runner import stage_config_payload
from agent_memory_orchestrator.reasoning_graph.retrieval import RetrievalDocument
from agent_memory_orchestrator.reasoning_graph.retrieval import RetrievalIndexStore


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


def test_production_semantic_eval_reports_stale_full_trace_state(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo_id = "repo:remote:test"
    store = V2SessionJobStore(settings)
    try:
        job = store.enqueue_session(
            session_id="s-prod",
            boundary_event_id="raw_boundary",
            repo_path=str(tmp_path / "repo"),
        ).job
        with connect(settings.db_path) as conn:
            conn.execute(
                "UPDATE v2_session_jobs SET repo_id=?, repo_path=? WHERE job_id=?",
                (repo_id, str(tmp_path / "repo"), job["job_id"]),
            )
            conn.commit()

        artifact_dir = Path(str(job["artifact_dir"]))
        kuzu_dir = artifact_dir / "kuzu_write"
        kuzu_dir.mkdir(parents=True, exist_ok=True)
        (kuzu_dir / "compact_graph_manifest.json").write_text(
            json.dumps({"nodes": [{"kind": "CodeNode"}], "edges": []}),
            encoding="utf-8",
        )
        (kuzu_dir / "kuzu_write_result.json").write_text(
            json.dumps({"source": "compact_manifest_fallback"}),
            encoding="utf-8",
        )
        quality_path = artifact_dir / "quality_eval" / "quality_eval.json"
        quality_path.parent.mkdir(parents=True, exist_ok=True)
        quality_path.write_text(
            json.dumps({"product_ready": False, "blocking_issues": ["curated_graph_manifest_missing"]}),
            encoding="utf-8",
        )
        store.start_stage(
            job_id=job["job_id"],
            stage="quality_eval",
            input_artifact=str(kuzu_dir / "compact_graph_manifest.json"),
            input_hash="input",
            stage_config_hash="config",
        )
        store.complete_stage(
            job_id=job["job_id"],
            stage="quality_eval",
            output_artifact=str(quality_path),
            output_hash="quality",
            diagnostics={"product_ready": False},
        )

        with connect(settings.retrieval_db_path) as conn:
            RetrievalIndexStore(conn).upsert_documents(
                [
                    RetrievalDocument(
                        doc_id="trace-code-node",
                        doc_type="session_codenode",
                        graph_node_id="code:if",
                        node_kind="CodeNode",
                        repo_id=repo_id,
                        packet_id="WP0001",
                        commit_sha="abc123",
                        title="If block",
                        body="raw low-level code node",
                    ),
                    RetrievalDocument(
                        doc_id="trace-code-hunk",
                        doc_type="session_codehunk",
                        graph_node_id="hunk:1",
                        node_kind="CodeHunk",
                        repo_id=repo_id,
                        packet_id="WP0001",
                        commit_sha="abc123",
                        title="Raw hunk",
                        body="raw patch hunk",
                    ),
                    RetrievalDocument(
                        doc_id="legacy-doc",
                        doc_type="session_packet",
                        graph_node_id="legacy:packet",
                        node_kind="Packet",
                        repo_id="",
                        packet_id="WP0002",
                        commit_sha="def456",
                        title="Legacy packet",
                        body="legacy global doc",
                    ),
                ]
            )

        out_path = tmp_path / "semantic_input_output_eval.json"
        report = run_production_semantic_eval(
            settings,
            job_id=job["job_id"],
            repo_id=repo_id,
            out_path=out_path,
        )

        assert out_path.exists()
        assert report["product_ready"] is False
        assert "curated_graph_manifest_missing" in report["blocked_issues"]
        assert "retrieval_full_trace_dominated" in report["blocked_issues"]
        assert "central_merge_not_applied" in report["blocked_issues"]
        assert report["retrieval"]["legacy_doc_count"] == 1
        assert report["retrieval"]["trace_doc_count"] == 2
    finally:
        store.close()


def test_stage_config_hashes_are_stage_specific(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    retrieval_changed = replace(settings, auto_retrieval_max_doc_chars=settings.auto_retrieval_max_doc_chars + 100)
    qwen_changed = replace(settings, qwen_model="qwen3.5:14b")

    assert stage_config_hash(retrieval_changed, stage="retrieval_docs") != stage_config_hash(settings, stage="retrieval_docs")
    assert stage_config_hash(retrieval_changed, stage="evidence_view") == stage_config_hash(settings, stage="evidence_view")
    assert stage_config_hash(retrieval_changed, stage="qwen_reasoning") == stage_config_hash(settings, stage="qwen_reasoning")
    assert stage_config_hash(qwen_changed, stage="qwen_reasoning") != stage_config_hash(settings, stage="qwen_reasoning")
    assert stage_config_hash(qwen_changed, stage="retrieval_docs") == stage_config_hash(settings, stage="retrieval_docs")

    retrieval_payload = stage_config_payload(settings, stage="retrieval_docs")
    assert "retrieval_projection_version" in retrieval_payload
    assert "qwen_model" not in retrieval_payload


def test_superseded_stage_validity_is_audited_without_new_status(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = V2SessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-rerun", boundary_event_id="raw_boundary").job
        artifact_dir = Path(str(job["artifact_dir"]))
        old_output = artifact_dir / "evidence_view" / "old.json"
        old_output.parent.mkdir(parents=True, exist_ok=True)
        old_output.write_text("{}", encoding="utf-8")
        store.start_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            input_artifact=str(settings.evidence_dir),
            input_hash="old-input",
            stage_config_hash="old-config",
        )
        store.complete_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            output_artifact=str(old_output),
            output_hash="old-output",
            diagnostics={"ok": True},
        )

        runner = V2SessionJobRunner(settings, job_store=store)

        def fake_stage(self: V2SessionJobRunner, job_row: dict[str, object], artifacts: Path, stage_dir: Path) -> StageResult:
            del self, job_row, artifacts
            output = stage_dir / "new.json"
            output.write_text("{}", encoding="utf-8")
            return StageResult(output_path=output, diagnostics={"ok": True})

        runner._stage_evidence_view = MethodType(fake_stage, runner)  # type: ignore[method-assign]
        result = runner._run_stage(job, "evidence_view", artifact_dir)
        store.complete_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            output_artifact=str(result.output_path),
            output_hash="new-output",
            diagnostics=result.diagnostics,
        )
        stage = store.stage_row(job_id=job["job_id"], stage="evidence_view")
        events = store.list_events(job["job_id"])

        assert stage is not None
        assert stage["status"] == "complete"
        assert stage["diagnostics"]["superseded_previous_stage"]["validity"] == "superseded"
        assert stage["diagnostics"]["superseded_previous_stage"]["reason"] == "input_hash_changed"
        assert any(event["event_type"] == "stage_superseded" for event in events)
    finally:
        store.close()
