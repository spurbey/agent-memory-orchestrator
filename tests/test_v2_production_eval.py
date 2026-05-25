from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.reasoning_graph.central_merge.production_eval import run_production_semantic_eval
from agent_memory_orchestrator.reasoning_graph.jobs import V2SessionJobStore
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
