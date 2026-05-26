from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import MethodType

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.reasoning_graph.central_merge.production_eval import run_production_semantic_eval
from agent_memory_orchestrator.reasoning_graph.jobs import V2SessionJobStore
from agent_memory_orchestrator.reasoning_graph.jobs.reset import initialize_fresh_v2_production_storage
from agent_memory_orchestrator.reasoning_graph.jobs.runner import StageResult
from agent_memory_orchestrator.reasoning_graph.jobs.runner import StageFailed
from agent_memory_orchestrator.reasoning_graph.jobs.runner import V2SessionJobRunner
from agent_memory_orchestrator.reasoning_graph.jobs.runner import _quality_issues
from agent_memory_orchestrator.reasoning_graph.jobs.runner import _quality_readiness
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
                        body="why graph_service.py changed raw low-level code node",
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
        assert "retrieval_query_raw_trace_top_result" in report["blocked_issues"]
        assert "retrieval_query_missing_curated_support" in report["blocked_issues"]
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


def test_central_merge_requires_curated_manifest(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = V2SessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-missing-curated", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        kuzu_dir = Path(str(job["artifact_dir"])) / "kuzu_write"
        kuzu_dir.mkdir(parents=True, exist_ok=True)
        (kuzu_dir / "compact_graph_manifest.json").write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
        (kuzu_dir / "kuzu_write_result.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        runner = V2SessionJobRunner(settings, job_store=store)

        try:
            runner._stage_central_version_merge(job, Path(str(job["artifact_dir"])), Path(str(job["artifact_dir"])) / "central_version_merge")
            raise AssertionError("central merge should reject full-trace-only input")
        except StageFailed as exc:
            assert exc.reason == "curated_graph_manifest_missing"
            assert exc.diagnostics["input_source"] == "missing_curated_graph_manifest"
    finally:
        store.close()


def test_retrieval_docs_read_curated_manifest_directly(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    initialize_fresh_v2_production_storage(settings)
    store = V2SessionJobStore(settings)
    try:
        repo_id = "repo:remote:curated"
        job = store.enqueue_session(session_id="s-curated-retrieval", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        store.update_job_repo_identity(job_id=job["job_id"], repo_path=str(tmp_path), repo_id=repo_id, reason="test", metadata={})
        job = store.get_job(job["job_id"]) or job
        kuzu_dir = Path(str(job["artifact_dir"])) / "kuzu_write"
        kuzu_dir.mkdir(parents=True, exist_ok=True)
        compact_manifest = {
            "nodes": [
                {"id": "code:if", "kind": "CodeNode", "label": "If", "summary": "raw trace node", "properties": {}},
            ],
            "edges": [],
        }
        curated_manifest = {
            "nodes": [
                {
                    "id": "file-impact:graph_service",
                    "kind": "FileImpactSummary",
                    "label": "graph_service.py impact",
                    "summary": "Graph service retrieval behavior changed.",
                    "properties": {"path": "src/agent_memory_orchestrator/graph_service.py", "packet_ids": ["WP0001"]},
                }
            ],
            "edges": [],
        }
        (kuzu_dir / "compact_graph_manifest.json").write_text(json.dumps(compact_manifest), encoding="utf-8")
        (kuzu_dir / "curated_graph_manifest.json").write_text(json.dumps(curated_manifest), encoding="utf-8")
        runner = V2SessionJobRunner(settings, job_store=store)
        retrieval_dir = Path(str(job["artifact_dir"])) / "retrieval_docs"
        retrieval_dir.mkdir(parents=True, exist_ok=True)

        result = runner._stage_retrieval_docs(job, Path(str(job["artifact_dir"])), retrieval_dir)

        assert result.diagnostics["retrieval_source"] == "curated_graph_manifest"
        assert result.diagnostics["doc_count"] == 1
        with connect(settings.retrieval_db_path) as conn:
            index = RetrievalIndexStore(conn)
            index.upsert_documents(
                [
                    RetrievalDocument(
                        doc_id="inactive-trace",
                        doc_type="session_codenode",
                        graph_node_id="code:if",
                        node_kind="CodeNode",
                        repo_id=repo_id,
                        title="Inactive trace",
                        body="This inactive trace should not be returned when an active projection exists.",
                        packet_id="WP0002",
                        commit_sha="abc123",
                    )
                ]
            )
            active_projection = index.active_projection(repo_id)
            docs = index.list_documents(repo_id=repo_id)
            rows = conn.execute("SELECT doc_type, node_kind FROM retrieval_documents WHERE repo_id=? ORDER BY doc_type", (repo_id,)).fetchall()
        assert active_projection is not None
        assert active_projection["projection_id"] == result.diagnostics["active_projection_id"]
        assert [(doc.doc_type, doc.node_kind) for doc in docs] == [("file_impact", "FileImpactSummary")]
        assert ("session_codenode", "CodeNode") in [(row["doc_type"], row["node_kind"]) for row in rows]
    finally:
        store.close()


def test_retrieval_projection_carries_forward_prior_curated_docs(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    initialize_fresh_v2_production_storage(settings)
    store = V2SessionJobStore(settings)
    try:
        repo_id = "repo:remote:cumulative"
        runner = V2SessionJobRunner(settings, job_store=store)

        def run_projection(session_id: str, node_id: str, title: str) -> dict[str, object]:
            job = store.enqueue_session(session_id=session_id, boundary_event_id=f"raw-{session_id}", repo_path=str(tmp_path)).job
            store.update_job_repo_identity(job_id=job["job_id"], repo_path=str(tmp_path), repo_id=repo_id, reason="test", metadata={})
            job = store.get_job(job["job_id"]) or job
            kuzu_dir = Path(str(job["artifact_dir"])) / "kuzu_write"
            kuzu_dir.mkdir(parents=True, exist_ok=True)
            curated_manifest = {
                "nodes": [
                    {
                        "id": node_id,
                        "kind": "FileImpactSummary",
                        "label": title,
                        "summary": title,
                        "properties": {"path": f"src/{node_id}.py", "source": "curated_graph_manifest"},
                    }
                ],
                "edges": [],
            }
            (kuzu_dir / "compact_graph_manifest.json").write_text(json.dumps(curated_manifest), encoding="utf-8")
            (kuzu_dir / "curated_graph_manifest.json").write_text(json.dumps(curated_manifest), encoding="utf-8")
            retrieval_dir = Path(str(job["artifact_dir"])) / "retrieval_docs"
            retrieval_dir.mkdir(parents=True, exist_ok=True)
            return runner._stage_retrieval_docs(job, Path(str(job["artifact_dir"])), retrieval_dir).diagnostics

        first = run_projection("s-one", "file-impact-one", "Control room web UI impact")
        second = run_projection("s-two", "file-impact-two", "Qwen JSON hardening impact")

        assert first["doc_count"] == 1
        assert second["current_doc_count"] == 1
        assert second["carried_forward_doc_count"] == 1
        assert second["doc_count"] == 2
        with connect(settings.retrieval_db_path) as conn:
            index = RetrievalIndexStore(conn)
            docs = index.list_documents(repo_id=repo_id)
        assert {doc.title for doc in docs} == {"Control room web UI impact", "Qwen JSON hardening impact"}
        assert {doc.projection_id for doc in docs} == {second["active_projection_id"]}
    finally:
        store.close()


def test_retrieval_projection_is_not_active_until_activation_gate_passes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    initialize_fresh_v2_production_storage(settings)
    store = V2SessionJobStore(settings)
    try:
        repo_id = "repo:remote:raw-curated"
        job = store.enqueue_session(session_id="s-raw-projection", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        store.update_job_repo_identity(job_id=job["job_id"], repo_path=str(tmp_path), repo_id=repo_id, reason="test", metadata={})
        job = store.get_job(job["job_id"]) or job
        kuzu_dir = Path(str(job["artifact_dir"])) / "kuzu_write"
        kuzu_dir.mkdir(parents=True, exist_ok=True)
        raw_manifest = {
            "nodes": [
                {
                    "id": "code:if",
                    "kind": "CodeNode",
                    "label": "If",
                    "summary": "raw trace node should not activate product retrieval",
                    "properties": {},
                }
            ],
            "edges": [],
        }
        (kuzu_dir / "compact_graph_manifest.json").write_text(json.dumps(raw_manifest), encoding="utf-8")
        (kuzu_dir / "curated_graph_manifest.json").write_text(json.dumps(raw_manifest), encoding="utf-8")
        runner = V2SessionJobRunner(settings, job_store=store)
        retrieval_dir = Path(str(job["artifact_dir"])) / "retrieval_docs"
        retrieval_dir.mkdir(parents=True, exist_ok=True)

        result = runner._stage_retrieval_docs(job, Path(str(job["artifact_dir"])), retrieval_dir)

        assert result.diagnostics["activation_gate"]["passed"] is False
        assert "retrieval_projection_contains_raw_trace_docs" in result.diagnostics["activation_gate"]["blocking_failures"]
        assert result.diagnostics["active_projection_id"] == ""
        with connect(settings.retrieval_db_path) as conn:
            index = RetrievalIndexStore(conn)
            projection = index.projection(result.diagnostics["projection_id"])
            assert projection["status"] == "review_required"
            assert index.active_projection(repo_id) is None
    finally:
        store.close()


def test_quality_readiness_reports_partial_vector_state() -> None:
    central = {
        "status": "applied",
        "mode": "apply_exact_atoms",
        "input_source": "curated_graph_manifest",
        "curated_input_hash": "curated",
    }
    retrieval = {
        "doc_count": 10,
        "retrieval_source": "curated_graph_manifest",
        "active_projection_id": "rproj:1",
    }
    embedding = {"total_docs": 10, "embedded": 3, "already_embedded": 2, "limit_hit": True}
    faiss = {"item_count": 5, "status": "partial"}

    issues = _quality_issues(
        central_result=central,
        retrieval_result=retrieval,
        embedding_result=embedding,
        faiss_result=faiss,
    )
    readiness = _quality_readiness(
        issues=issues,
        central_result=central,
        retrieval_result=retrieval,
        embedding_result=embedding,
        faiss_result=faiss,
    )

    assert {issue["code"] for issue in issues} == {"embedding_coverage_partial", "faiss_coverage_partial"}
    assert readiness == {
        "mechanical_complete": True,
        "lexical_retrieval_ready": True,
        "vector_retrieval_ready": False,
        "central_memory_ready": True,
        "answer_trace_ready": True,
        "product_ready": False,
    }
