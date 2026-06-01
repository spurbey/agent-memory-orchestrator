from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.runtime.daemon import auto_drain as auto_drain_module
from agent_memory_orchestrator.runtime.daemon import server as daemon_module
from agent_memory_orchestrator.core.db import connect
from agent_memory_orchestrator.application.pipeline import job_runner as runner_module
from agent_memory_orchestrator.application.pipeline.debug.backfill import backfill_central_merge_plan
from agent_memory_orchestrator.application.pipeline.debug.fixtures import export_job_fixture
from agent_memory_orchestrator.application.pipeline.evaluation.semantic_fixture import judge_semantic_case
from agent_memory_orchestrator.application.pipeline.evaluation.semantic_fixture import run_semantic_eval_fixture
from agent_memory_orchestrator.application.pipeline.job_runner import ProductionSessionJobRunner
from agent_memory_orchestrator.application.pipeline.job_runner import require_complete_production_marker
from agent_memory_orchestrator.application.pipeline.storage_lifecycle import adopt_existing_production_storage
from agent_memory_orchestrator.application.pipeline.storage_lifecycle import initialize_fresh_production_storage
from agent_memory_orchestrator.application.pipeline.storage_lifecycle import reset_production_storage
from agent_memory_orchestrator.infrastructure.faiss.embedding_store import GraphEmbeddingRecord
from agent_memory_orchestrator.infrastructure.faiss.embedding_store import GraphEmbeddingStore
from agent_memory_orchestrator.infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from agent_memory_orchestrator.domain.versioning.graph_views import graph_view_id
from agent_memory_orchestrator.domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from agent_memory_orchestrator.domain.pipeline.constants import PIPELINE_VERSION
from agent_memory_orchestrator.application.services.central_merge import apply as applier_module
from agent_memory_orchestrator.application.services.central_merge.apply import apply_merge_plan
from agent_memory_orchestrator.application.services.central_merge.apply import CentralMergeApplyError
from agent_memory_orchestrator.application.services.central_merge.apply import repo_central_graph_path
from agent_memory_orchestrator.domain.versioning.central_merge.planner import build_dry_run_merge_plan
from agent_memory_orchestrator.domain.versioning.repo_identity import normalize_remote_url
from agent_memory_orchestrator.domain.reasoning.extraction import QWEN_REASONING_CONTRACT_VERSION
from agent_memory_orchestrator.domain.reasoning.extraction import build_qwen_reasoning_packet_prompt
from agent_memory_orchestrator.domain.reasoning.extraction import qwen_reasoning_contract_hash
from agent_memory_orchestrator.domain.retrieval.models import RetrievalDocument
from agent_memory_orchestrator.infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from agent_memory_orchestrator.application.services.session.detail import build_session_detail_fallback
from agent_memory_orchestrator.application.services.session.detail import _load_session_evidence_records
from agent_memory_orchestrator.application.services.session.detail import _session_pending_summary
from agent_memory_orchestrator.infrastructure.kuzu import GraphNode
from agent_memory_orchestrator.infrastructure.kuzu import InMemoryGraphStore


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


def _product_plan(plan):
    payload = plan.as_dict()
    return {
        **payload,
        "input_source": "curated_graph_manifest",
        "curated_input_hash": "curated-input",
        "trace_input_hash": "trace-input",
    }


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def test_production_enqueue_is_idempotent_and_atomic_lock_skips_locked_failed_and_pending_model(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        first = store.enqueue_session(
            session_id="s1",
            boundary_event_id="raw_boundary_1",
            source_app="codex",
            repo_path=str(tmp_path),
            source_evidence_day="2026-05-20",
            source_first_event_id="raw_first",
            source_latest_event_id="raw_latest",
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
        assert first.job["source_first_event_id"] == "raw_first"
        assert first.job["source_latest_event_id"] == "raw_latest"

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


def test_session_records_filter_by_evidence_days_and_event_bounds(tmp_path: Path) -> None:
    evidence_dir = tmp_path / ".evidence"
    evidence_dir.mkdir()
    _write_jsonl(
        evidence_dir / "2026-05-19.jsonl",
        [
            {"id": "raw_old", "session_id": "s1"},
            {"id": "raw_other_old", "session_id": "other"},
        ],
    )
    _write_jsonl(
        evidence_dir / "2026-05-20.jsonl",
        [
            {"id": "raw_before", "session_id": "s1"},
            {"id": "raw_first", "session_id": "s1"},
            {"id": "raw_middle", "session_id": "s1"},
            {"id": "raw_other", "session_id": "other"},
            {"id": "raw_latest", "session_id": "s1"},
            {"id": "raw_after", "session_id": "s1"},
        ],
    )

    records = runner_module._session_records(
        evidence_dir,
        "s1",
        evidence_days=["2026-05-20"],
        first_event_id="raw_first",
        latest_event_id="raw_latest",
    )

    assert [record["id"] for record in records] == ["raw_first", "raw_middle", "raw_latest"]

    assert (
        runner_module._session_records(
            evidence_dir,
            "s1",
            evidence_days=["2026-05-20"],
            first_event_id="raw_missing",
            latest_event_id="raw_latest",
        )
        == []
    )


def test_production_stage_rows_track_hashes_and_config_hash(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
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


def test_production_qwen_reasoning_reuses_existing_matching_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-qwen", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        artifact_dir = Path(job["artifact_dir"])
        work_dir = artifact_dir / "work_packets"
        work_dir.mkdir(parents=True, exist_ok=True)
        packets = [
            {
                "packet_id": "WP0001",
                "commit": {"short_sha": "abc123", "full_sha": "abc123"},
                "summary": "Add graph retrieval.",
                "problem_refs": [],
                "rationale_refs": [],
                "validation_refs": [],
            }
        ]
        work_output = work_dir / "reasoning_work_packets.json"
        work_output.write_text(json.dumps(packets), encoding="utf-8")
        store.start_stage(
            job_id=job["job_id"],
            stage="work_packets",
            input_artifact="view.json",
            input_hash="input",
            stage_config_hash="config",
        )
        store.complete_stage(
            job_id=job["job_id"],
            stage="work_packets",
            output_artifact=str(work_output),
            output_hash="output",
            diagnostics={"packet_count": 1},
        )

        qwen_dir = artifact_dir / "qwen_reasoning"
        qwen_dir.mkdir(parents=True, exist_ok=True)
        contract = runner_module._qwen_contract(settings)
        packet_key = runner_module._qwen_packet_key(packets[0], contract=contract)
        qwen_output = qwen_dir / "stage4_packet_reasoning_results.json"
        qwen_output.write_text(
            json.dumps(
                [
                    {
                        "packet_id": "WP0001",
                        "commit_sha": "abc123",
                        "model": "qwen3:1.7b",
                        "runtime": "ollama",
                        "contract_hash": contract["contract_hash"],
                        "parsed_output": {"kind": "decision", "summary": "Use graph retrieval."},
                    }
                ]
            ),
            encoding="utf-8",
        )
        (qwen_dir / "stage4_packet_reasoning_manifest.json").write_text(
            json.dumps({"complete": True, "result_count": 1, "packet_count": 1, "contract": contract, "packets": [packet_key]}),
            encoding="utf-8",
        )

        class FailingQwenClient:
            def __init__(self, **_: object) -> None:
                pass

            def generate_json(self, *_: object, **__: object) -> dict[str, object]:
                raise AssertionError("existing matching checkpoint should be reused")

        monkeypatch.setattr(runner_module, "OllamaQwenClient", FailingQwenClient)
        runner = ProductionSessionJobRunner(settings, job_store=store)

        run = runner.run_next()
        stage = store.stage_row(job_id=job["job_id"], stage="qwen_reasoning")

        assert run["stage"] == "qwen_reasoning"
        assert run["status"] == "pending"
        assert stage is not None
        assert stage["status"] == "complete"
        assert stage["diagnostics"]["reused_result_count"] == 1
        assert stage["diagnostics"]["generated_result_count"] == 0
        assert (qwen_dir / "stage4_packet_reasoning_manifest.json").exists()
    finally:
        store.close()


def test_production_schema_adds_central_merge_control_tables(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        rows = store.conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        store.close()
    names = {row["name"] for row in rows}

    assert {
        "v2_central_merge_plans",
        "v2_central_review_candidates",
        "v2_graph_commits",
        "v2_graph_views",
        "v2_central_merge_locks",
        "v2_semantic_eval_runs",
        "v2_semantic_eval_cases",
        "v2_semantic_eval_judgments",
    }.issubset(names)


def test_production_jobs_and_repository_list_are_repo_scoped(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    store = ProductionSessionJobStore(settings)
    try:
        job_a = store.enqueue_session(
            session_id="s-repo-a",
            boundary_event_id="raw_a",
            repo_path=str(repo_a),
            repo_id="repo:test:a",
        ).job
        job_b = store.enqueue_session(
            session_id="s-repo-b",
            boundary_event_id="raw_b",
            repo_path=str(repo_b),
            repo_id="repo:test:b",
        ).job

        scoped = store.list_jobs(repo_id=job_a["repo_id"])
        repos = store.list_repositories()
    finally:
        store.close()

    assert [job["session_id"] for job in scoped] == ["s-repo-a"]
    assert {row["repo_id"] for row in repos} >= {job_a["repo_id"], job_b["repo_id"]}


def test_production_central_merge_stage_writes_plan_and_applies_exact_atoms(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-central", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        artifact_dir = Path(job["artifact_dir"])
        kuzu_dir = artifact_dir / "kuzu_write"
        kuzu_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "nodes": [
                {"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "ABC123"}},
                {"id": "hunk:1", "kind": "CodeHunk", "properties": {"path": "src/graph_service.py"}},
                {"id": "symbol:GraphRagService", "kind": "Symbol", "properties": {"path": "src/graph_service.py", "qualified_name": "GraphRagService"}},
                {"id": "code:GraphRagService.init", "kind": "CodeNode", "properties": {"path": "src/graph_service.py", "qualified_name": "GraphRagService.__init__", "symbol_kind": "function"}},
                {"id": "decision:1", "kind": "ReasoningNode", "properties": {"summary": "Do not canonicalize decisions yet."}},
            ],
            "edges": [],
            "inventory": {"node_count": 5, "edge_count": 0, "unresolved_edge_count": 0},
        }
        (kuzu_dir / "compact_graph_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (kuzu_dir / "curated_graph_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (kuzu_dir / "curation_audit.json").write_text(json.dumps({"policy": "test"}), encoding="utf-8")
        result_path = kuzu_dir / "kuzu_write_result.json"
        result_path.write_text(json.dumps({"ok": True, "inventory": manifest["inventory"]}), encoding="utf-8")
        store.start_stage(
            job_id=job["job_id"],
            stage="kuzu_write",
            input_artifact="graph_edges.json",
            input_hash="input",
            stage_config_hash="config",
        )
        store.complete_stage(
            job_id=job["job_id"],
            stage="kuzu_write",
            output_artifact=str(result_path),
            output_hash="output",
            diagnostics=manifest["inventory"],
        )

        runner = ProductionSessionJobRunner(settings, job_store=store)
        run = runner.run_next()

        assert run["stage"] == "central_version_merge"
        assert run["status"] == "pending"
        plan_row = store.get_central_merge_plan_for_job(job["job_id"])
        assert plan_row is not None
        assert plan_row["status"] == "applied"
        assert plan_row["mode"] == "apply_exact_atoms"
        assert plan_row["metrics"]["exact_atom_created_count"] == 4
        assert plan_row["metrics"]["review_candidate_count"] == 0
        assert plan_row["plan"]["new_atoms"]
        assert plan_row["plan"]["new_versions"]
        view = store.graph_view(repo_id=plan_row["repo_id"], branch="main", mode="active")
        assert view is not None
        assert view["graph_commit_id"]
        stage = store.stage_row(job_id=job["job_id"], stage="central_version_merge")
        assert stage is not None
        assert Path(stage["output_artifact"]).name == "merge_plan.json"
        assert stage["diagnostics"]["mode"] == "apply_exact_atoms"
        assert stage["diagnostics"]["graph_commit_id"] == view["graph_commit_id"]
        merge_result = artifact_dir / "central_version_merge" / "merge_result.json"
        assert merge_result.exists()
        merge_payload = json.loads(merge_result.read_text(encoding="utf-8"))
        assert merge_payload["status"] == "applied"
        assert merge_payload["input_source"] == "curated_graph_manifest"
        fixture = export_job_fixture(settings, job_id=job["job_id"], out_dir=tmp_path / "fixture")
        semantic_context = fixture["fixture"]["semantic_context"]
        assert semantic_context["central_version_merge"]["repo_id"].startswith("repo:")
        assert semantic_context["central_version_merge"]["applied"] is True
        result = run_semantic_eval_fixture(fixture_path=Path(fixture["path"]), case_set="baseline")
        assert result["status"] == "passed"
        assert result["metrics"]["case_count"] >= 5
    finally:
        store.close()


def test_production_central_merge_input_hash_tracks_active_graph_view_head(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-head-hash", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        input_artifact = tmp_path / "kuzu_write_result.json"
        input_artifact.write_text(json.dumps({"ok": True}), encoding="utf-8")
        runner = ProductionSessionJobRunner(settings, job_store=store)

        initial_hash = runner._stage_input_hash(job=job, stage="central_version_merge", input_artifact=input_artifact)
        unchanged_stage_hash = runner._stage_input_hash(job=job, stage="retrieval_docs", input_artifact=input_artifact)
        store.update_graph_view_head(
            repo_id=str(job.get("repo_id") or ""),
            graph_commit_id="v2gcommit:new-head",
            metadata={"test": "head-input"},
        )
        updated_hash = runner._stage_input_hash(job=job, stage="central_version_merge", input_artifact=input_artifact)

        assert updated_hash != initial_hash
        assert runner._stage_input_hash(job=job, stage="retrieval_docs", input_artifact=input_artifact) == unchanged_stage_hash
    finally:
        store.close()


def test_production_central_merge_apply_writes_exact_atoms_graph_commit_and_view(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        graph.upsert_node(GraphNode(id="jobprefix:commit:abc123", kind="Commit", label="abc123"))
        job = store.enqueue_session(session_id="s-apply", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        compact_graph = {
            "nodes": [
                {"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}},
                {"id": "hunk:1", "kind": "CodeHunk", "properties": {"path": "src/graph_service.py"}},
                {"id": "symbol:GraphRagService", "kind": "Symbol", "properties": {"path": "src/graph_service.py", "qualified_name": "GraphRagService"}},
            ],
            "edges": [],
        }
        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="")
        stored = store.upsert_central_merge_plan(_product_plan(plan))

        applied = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)

        assert applied["ok"] is True
        assert applied["status"] == "applied"
        result_artifact = Path(applied["result_artifact"])
        assert result_artifact.name == "merge_result.json"
        assert result_artifact.exists()
        merge_result = json.loads(result_artifact.read_text(encoding="utf-8"))
        assert merge_result["status"] == "applied"
        assert applied["graph_commit_id"] == applied["graph_commit"]["graph_commit_id"]
        assert merge_result["graph_commit_id"] == applied["graph_commit"]["graph_commit_id"]
        assert merge_result["graph_commit"]["graph_commit_id"] == applied["graph_commit"]["graph_commit_id"]
        assert merge_result["input_source"] == "curated_graph_manifest"
        assert merge_result["curated_input_hash"] == "curated-input"
        updated_plan = store.get_central_merge_plan(stored["plan_id"])
        assert updated_plan is not None
        assert updated_plan["status"] == "applied"
        view = store.graph_view(repo_id=applied["repo_id"], branch="main", mode="active")
        assert view is not None
        assert view["graph_commit_id"] == applied["graph_commit"]["graph_commit_id"]
        assert graph.nodes[applied["graph_commit"]["graph_commit_id"]].kind == "GraphCommit"
        graph_view_node_id = graph_view_id(repo_id=applied["repo_id"], branch="main", mode="active")
        assert graph.nodes[graph_view_node_id].kind == "GraphView"
        assert graph_view_node_id in applied["graph_commit"]["added_nodes"]
        atom_nodes = [node for node in graph.nodes.values() if node.kind == "KnowledgeAtom"]
        version_nodes = [node for node in graph.nodes.values() if node.kind == "KnowledgeVersion"]
        central_nodes = [
            node
            for node in graph.nodes.values()
            if node.kind in {"KnowledgeAtom", "KnowledgeVersion", "GraphCommit", "GraphView"}
        ]
        assert {node.metadata["atom_kind"] for node in atom_nodes} == {"commit", "file"}
        assert applied["deferred_atom_counts"]["symbol"] == 1
        assert applied["applied_atom_counts"]["commit"] == 1
        assert applied["applied_atom_counts"]["file"] == 1
        assert applied["applied_atom_counts"]["decision"] == 0
        assert applied["applied_version_counts"]["commit"] == 1
        assert applied["applied_version_counts"]["file"] == 1
        assert applied["applied_version_counts"]["decision"] == 0
        assert all(node.metadata["graph_commit_id"] == applied["graph_commit"]["graph_commit_id"] for node in atom_nodes + version_nodes)
        assert all(node.metadata["repo_id"].startswith("repo:") for node in atom_nodes)
        assert all(node.metadata.get("idempotency_key") for node in central_nodes)
        assert any(edge.kind == "VERSION_OF" for edge in graph.edges.values())
        assert any(edge.kind == "DERIVED_FROM_SESSION_NODE" for edge in graph.edges.values())
        derived_targets = {edge.target_id for edge in graph.edges.values() if edge.kind == "DERIVED_FROM_SESSION_NODE"}
        assert "jobprefix:commit:abc123" in derived_targets
        assert "commit:abc123" not in derived_targets
        fixture = export_job_fixture(settings, job_id=job["job_id"], out_dir=tmp_path / "applied-fixture")
        central = fixture["fixture"]["semantic_context"]["central_version_merge"]
        assert central["applied"] is True
        assert central["status"] == "applied"
        assert central["mode"] == "apply_exact_atoms"
        assert central["graph_commit_id"] == applied["graph_commit"]["graph_commit_id"]
        assert central["active_graph_view_head"] == applied["graph_commit"]["graph_commit_id"]
        assert central["graph_commit_status"] == "applied"

        node_count = len(graph.nodes)
        edge_count = len(graph.edges)
        second = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)
        assert second["ok"] is True
        assert second["idempotent"] is True
        assert len(graph.nodes) == node_count
        assert len(graph.edges) == edge_count
    finally:
        store.close()


def test_production_central_merge_apply_defaults_to_repo_scoped_central_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = make_settings(tmp_path)
    opened_paths: list[Path] = []

    class CapturingGraphStore(InMemoryGraphStore):
        def __init__(self, graph_path: Path) -> None:
            super().__init__()
            opened_paths.append(graph_path)

    monkeypatch.setattr(applier_module, "KuzuGraphStore", CapturingGraphStore)
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-repo-central-graph", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        compact_graph = {
            "nodes": [
                {"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}},
                {"id": "hunk:1", "kind": "CodeHunk", "properties": {"path": "src/graph_service.py"}},
            ],
            "edges": [],
        }
        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="")
        stored = store.upsert_central_merge_plan(_product_plan(plan))

        applied = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store)

        expected_path = repo_central_graph_path(settings, applied["repo_id"])
        assert opened_paths == [expected_path]
        assert expected_path != settings.graph_path
        assert applied["central_graph_path"] == str(expected_path)
        assert applied["ok"] is True
    finally:
        store.close()


def test_production_central_merge_apply_requires_matching_graph_view_head(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        first_job = store.enqueue_session(session_id="s-apply-a", boundary_event_id="raw_boundary_a", repo_path=str(tmp_path)).job
        second_job = store.enqueue_session(session_id="s-apply-b", boundary_event_id="raw_boundary_b", repo_path=str(tmp_path)).job
        compact_graph = {"nodes": [{"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}}], "edges": []}
        first_plan = build_dry_run_merge_plan(job=first_job, compact_graph=compact_graph, parent_graph_commit_id="")
        second_plan = build_dry_run_merge_plan(job=second_job, compact_graph=compact_graph, parent_graph_commit_id="")
        first = store.upsert_central_merge_plan(_product_plan(first_plan))
        second = store.upsert_central_merge_plan(_product_plan(second_plan))

        applied = apply_merge_plan(settings=settings, plan_id=first["plan_id"], store=store, graph_store=graph)
        conflicted = apply_merge_plan(settings=settings, plan_id=second["plan_id"], store=store, graph_store=graph)

        assert applied["ok"] is True
        assert conflicted["ok"] is False
        assert conflicted["status"] == "failed_recoverable"
        assert conflicted["error"]["reason"] == "replan_required"
        failed_plan = store.get_central_merge_plan(second["plan_id"])
        assert failed_plan is not None
        assert failed_plan["status"] == "failed_recoverable"
    finally:
        store.close()


def test_production_central_merge_apply_rejects_non_curated_plan_input(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-apply-input", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        compact_graph = {"nodes": [{"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}}], "edges": []}
        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="")
        stored = store.upsert_central_merge_plan({**plan.as_dict(), "input_source": "compact_graph_manifest", "trace_input_hash": "trace"})

        with pytest.raises(CentralMergeApplyError, match="central_merge_plan_input_is_not_curated"):
            apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)

        stored_missing_hash = store.upsert_central_merge_plan({**plan.as_dict(), "input_source": "curated_graph_manifest"})
        with pytest.raises(CentralMergeApplyError, match="central_merge_plan_missing_curated_input_hash"):
            apply_merge_plan(settings=settings, plan_id=stored_missing_hash["plan_id"], store=store, graph_store=graph)
    finally:
        store.close()


def test_production_central_merge_planner_reports_matched_exact_atoms(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        first_job = store.enqueue_session(session_id="s-match-a", boundary_event_id="raw_boundary_a", repo_path=str(tmp_path)).job
        second_job = store.enqueue_session(session_id="s-match-b", boundary_event_id="raw_boundary_b", repo_path=str(tmp_path)).job
        compact_graph = {"nodes": [{"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}}], "edges": []}
        first_plan = build_dry_run_merge_plan(job=first_job, compact_graph=compact_graph, parent_graph_commit_id="")
        existing = {first_plan.new_atoms[0]["canonical_key"]: first_plan.new_atoms[0]}

        second_plan = build_dry_run_merge_plan(
            job=second_job,
            compact_graph=compact_graph,
            parent_graph_commit_id="v2gcommit:parent",
            existing_atoms_by_canonical_key=existing,
        )

        assert second_plan.new_atoms == []
        assert len(second_plan.matched_atoms) == 1
        assert second_plan.matched_atoms[0]["match_reason"] == "canonical_key_exact"
        assert second_plan.metrics["exact_atom_created_count"] == 0
        assert second_plan.metrics["exact_atom_matched_count"] == 1
        assert second_plan.new_versions[0]["atom_id"] == first_plan.new_atoms[0]["atom_id"]
    finally:
        store.close()


def test_production_central_merge_file_version_key_includes_producing_commit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-file-version-key", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        compact_graph = {
            "nodes": [
                {
                    "id": "file:hook",
                    "kind": "FileRef",
                    "properties": {
                        "path": "src/agent_memory_orchestrator/hook.py",
                        "commit_sha": "abc1234",
                    },
                }
            ],
            "edges": [],
        }

        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="")

        version_metadata = plan.new_versions[0]["metadata"]
        assert version_metadata["canonical_key"].endswith("|src/agent_memory_orchestrator/hook.py")
        assert version_metadata["producing_commit_sha"] == "abc1234"
        assert version_metadata["version_key"].endswith("|src/agent_memory_orchestrator/hook.py|abc1234")
    finally:
        store.close()


def test_production_central_merge_apply_attaches_versions_to_matched_atoms(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        first_job = store.enqueue_session(session_id="s-match-apply-a", boundary_event_id="raw_boundary_a", repo_path=str(tmp_path)).job
        second_job = store.enqueue_session(session_id="s-match-apply-b", boundary_event_id="raw_boundary_b", repo_path=str(tmp_path)).job
        compact_graph = {"nodes": [{"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}}], "edges": []}
        first_plan = build_dry_run_merge_plan(job=first_job, compact_graph=compact_graph, parent_graph_commit_id="")
        existing_atom = first_plan.new_atoms[0]
        graph.upsert_node(
            GraphNode(
                id=existing_atom["atom_id"],
                kind="KnowledgeAtom",
                label="abc123",
                status="active",
                scope="central",
                metadata={
                    "atom_kind": "commit",
                    "repo_id": existing_atom["repo_id"],
                    "canonical_key": existing_atom["canonical_key"],
                    "canonical_key_version": existing_atom["canonical_key_version"],
                },
            )
        )
        second_plan = build_dry_run_merge_plan(
            job=second_job,
            compact_graph=compact_graph,
            parent_graph_commit_id="",
            existing_atoms_by_canonical_key={existing_atom["canonical_key"]: existing_atom},
        )
        stored = store.upsert_central_merge_plan(_product_plan(second_plan))

        applied = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)

        assert applied["ok"] is True
        atom_nodes = [node for node in graph.nodes.values() if node.kind == "KnowledgeAtom"]
        assert len(atom_nodes) == 1
        version_nodes = [node for node in graph.nodes.values() if node.kind == "KnowledgeVersion"]
        assert len(version_nodes) == 1
        version_of_edges = [edge for edge in graph.edges.values() if edge.kind == "VERSION_OF"]
        assert len(version_of_edges) == 1
        assert version_of_edges[0].target_id == existing_atom["atom_id"]
    finally:
        store.close()


def test_production_central_merge_apply_writes_review_decision_versions_and_relation_edges(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-decision-apply", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        compact_graph = {
            "nodes": [
                {
                    "id": "reason:WP0001:abc:00",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Qwen JSON hardening",
                        "statement": "Disable Ollama thinking for Qwen JSON calls.",
                        "selected_files": ["src/agent_memory_orchestrator/llm/qwen.py"],
                    },
                },
                {
                    "id": "reason:WP0002:def:00",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Qwen JSON hardening",
                        "statement": "Disable Ollama thinking for Qwen JSON calls.",
                        "selected_files": ["src/agent_memory_orchestrator/llm/qwen.py"],
                    },
                },
            ],
            "edges": [],
        }
        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="")
        stored = store.upsert_central_merge_plan(_product_plan(plan))

        applied = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)

        decision_atoms = [node for node in graph.nodes.values() if node.kind == "KnowledgeAtom" and node.metadata.get("atom_kind") == "decision"]
        decision_versions = [node for node in graph.nodes.values() if node.kind == "KnowledgeVersion" and node.metadata.get("atom_kind") == "decision"]
        relation_edges = [edge for edge in graph.edges.values() if edge.kind == "DUPLICATE_OF"]
        assert applied["applied_atom_counts"]["decision"] == 1
        assert applied["applied_version_counts"]["decision"] == 2
        assert len(decision_atoms) == 1
        assert len(decision_versions) == 2
        assert {node.status for node in decision_versions} == {"review"}
        assert relation_edges
        assert relation_edges[0].metadata["status"] == "review"
    finally:
        store.close()


def test_production_central_merge_apply_status_changes_for_safe_supersedes(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-decision-supersedes", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        repo_id = str(job["repo_id"])
        old_metadata = {
            "repo_id": repo_id,
            "atom_kind": "decision",
            "status": "active",
            "version_metadata": {
                "node_type": "Decision",
                "subject": "Graph retrieval design",
                "statement": "Use session graph retrieval for graph queries.",
                "linked_files": ["src/agent_memory_orchestrator/graph/service.py"],
            },
        }
        old_central = {
            "id": "kver:old-central-graph-retrieval",
            "kind": "KnowledgeVersion",
            "status": "active",
            "metadata": old_metadata,
        }
        graph.upsert_node(
            GraphNode(
                id=old_central["id"],
                kind="KnowledgeVersion",
                label="Use session graph retrieval for graph queries.",
                status="active",
                scope="central",
                metadata=old_metadata,
            )
        )
        compact_graph = {
            "nodes": [
                {
                    "id": "reason:new",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Graph retrieval design",
                        "statement": "Replace session graph retrieval with central active GraphView retrieval.",
                        "selected_files": ["src/agent_memory_orchestrator/graph/service.py"],
                    },
                },
            ],
            "edges": [],
        }
        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="", active_central_versions=[old_central])
        stored = store.upsert_central_merge_plan(_product_plan(plan))

        applied = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)

        decision_versions = [node for node in graph.nodes.values() if node.kind == "KnowledgeVersion" and node.metadata.get("atom_kind") == "decision"]
        by_statement = {node.metadata.get("version_metadata", {}).get("statement"): node for node in decision_versions}
        status_edges = [edge for edge in graph.edges.values() if edge.kind == "STATUS_CHANGED"]
        assert applied["status_update_count"] == 2
        assert by_statement["Replace session graph retrieval with central active GraphView retrieval."].status == "active"
        assert by_statement["Use session graph retrieval for graph queries."].status == "superseded"
        assert {edge.metadata["new_status"] for edge in status_edges} == {"active", "superseded"}
        assert applied["status_updates"]
    finally:
        store.close()


def test_production_central_merge_keeps_same_session_refinement_in_review(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    graph = InMemoryGraphStore()
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-decision-same-session-refines", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        compact_graph = {
            "nodes": [
                {
                    "id": "reason:old",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Reasoning graph plan",
                        "statement": "Implement a reasoning graph documentation system.",
                        "selected_files": ["src/agent_memory_orchestrator/reasoning_graph/timeline.py"],
                    },
                },
                {
                    "id": "reason:new",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Reasoning graph plan",
                        "statement": "Implement a tighter reasoning graph documentation system with validation gates.",
                        "selected_files": ["src/agent_memory_orchestrator/reasoning_graph/timeline.py"],
                    },
                },
            ],
            "edges": [],
        }
        plan = build_dry_run_merge_plan(job=job, compact_graph=compact_graph, parent_graph_commit_id="")
        stored = store.upsert_central_merge_plan(_product_plan(plan))

        applied = apply_merge_plan(settings=settings, plan_id=stored["plan_id"], store=store, graph_store=graph)

        decision_versions = [node for node in graph.nodes.values() if node.kind == "KnowledgeVersion" and node.metadata.get("atom_kind") == "decision"]
        relation_edges = [edge for edge in graph.edges.values() if edge.kind in {"REFINES", "DUPLICATE_OF", "RELATED_REVIEW"}]
        assert relation_edges
        assert applied["status_update_count"] == 0
        assert {node.status for node in decision_versions} == {"review"}
    finally:
        store.close()


def test_production_repo_identity_normalizes_remote_urls() -> None:
    assert normalize_remote_url("git@github.com:Spurbey/Agent-Memory-Orchestrator.git") == "https://github.com/Spurbey/Agent-Memory-Orchestrator"
    assert normalize_remote_url("https://github.com/spurbey/agent-memory-orchestrator.git/") == "https://github.com/spurbey/agent-memory-orchestrator"


def test_production_central_merge_backfill_does_not_reopen_completed_job(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-old-complete", boundary_event_id="raw_boundary", repo_path=str(tmp_path)).job
        artifact_dir = Path(job["artifact_dir"])
        kuzu_dir = artifact_dir / "kuzu_write"
        kuzu_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "nodes": [{"id": "commit:abc123", "kind": "Commit", "properties": {"full_sha": "abc123"}}],
            "edges": [],
            "inventory": {"node_count": 1, "edge_count": 0, "unresolved_edge_count": 0},
        }
        (kuzu_dir / "compact_graph_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (kuzu_dir / "kuzu_write_result.json").write_text(json.dumps({"ok": True, "inventory": manifest["inventory"]}), encoding="utf-8")
        store.conn.execute(
            "UPDATE v2_session_jobs SET status='complete', current_stage='', last_successful_stage='quality_eval' WHERE job_id=?",
            (job["job_id"],),
        )
        store.conn.commit()
    finally:
        store.close()

    result = backfill_central_merge_plan(settings, job_id=job["job_id"], forced_by="test")

    store = ProductionSessionJobStore(settings)
    try:
        updated = store.get_job(job["job_id"])
        stage = store.stage_row(job_id=job["job_id"], stage="central_version_merge")
        events = store.list_events(job["job_id"], limit=5)
    finally:
        store.close()

    assert result["ok"] is True
    assert updated is not None
    assert updated["status"] == "complete"
    assert updated["current_stage"] == ""
    assert stage is not None
    assert stage["status"] == "complete"
    assert stage["diagnostics"]["backfilled"] is True
    assert any(event["event_type"] == "central_merge_backfilled" for event in events)


def test_production_central_merge_persists_decision_frames_for_cross_session_dry_run(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        first_job = store.enqueue_session(session_id="s-decision-a", boundary_event_id="raw_boundary_a", repo_path=str(tmp_path)).job
        first_graph = {
            "nodes": [
                {
                    "id": "reason:WP0001:abc:00",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Qwen JSON hardening",
                        "statement": "Disable Ollama thinking for Qwen JSON calls.",
                        "selected_files": ["src/agent_memory_orchestrator/llm/qwen.py"],
                    },
                }
            ],
            "edges": [],
        }
        first_plan = build_dry_run_merge_plan(job=first_job, compact_graph=first_graph, parent_graph_commit_id="")
        first_stored = store.upsert_central_merge_plan(_product_plan(first_plan))

        frames = store.list_decision_frames(repo_id=first_stored["repo_id"])

        assert len(frames) == 1
        assert frames[0]["frame"]["subject"] == "Qwen JSON hardening"
        assert frames[0]["status"] == "review"

        second_job = store.enqueue_session(session_id="s-decision-b", boundary_event_id="raw_boundary_b", repo_path=str(tmp_path)).job
        second_graph = {
            "nodes": [
                {
                    "id": "reason:WP0002:def:00",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Qwen JSON hardening",
                        "statement": "Disable Ollama thinking for Qwen JSON calls.",
                        "selected_files": ["src/agent_memory_orchestrator/llm/qwen.py"],
                    },
                }
            ],
            "edges": [],
        }
        second_plan = build_dry_run_merge_plan(
            job=second_job,
            compact_graph=second_graph,
            parent_graph_commit_id="",
            historical_decision_frames=store.list_decision_frames(repo_id=first_stored["repo_id"], exclude_job_id=second_job["job_id"]),
        )

        assert second_plan.metrics["historical_decision_frame_count"] == 1
        assert second_plan.metrics["decision_candidate_count"] == 1
        assert second_plan.review_candidates[0]["proposed_relation"] == "DUPLICATE_OF"
        assert second_plan.review_candidates[0]["score"]["target_scope"] == "decision_frame_ledger"

        third_job = store.enqueue_session(session_id="s-decision-c", boundary_event_id="raw_boundary_c", repo_path=str(tmp_path)).job
        third_graph = {
            "nodes": [
                {
                    "id": "reason:WP0003:ghi:00",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Installer output",
                        "statement": "Simplify first-run installer output.",
                        "selected_files": ["npm/agent-memory-orchestrator-cli/bin/cli.js"],
                    },
                },
                {
                    "id": "reason:WP0004:jkl:00",
                    "kind": "ReasoningNode",
                    "properties": {
                        "node_type": "Decision",
                        "status": "accepted",
                        "subject": "Installer output",
                        "statement": "Simplify first-run installer output.",
                        "selected_files": ["npm/agent-memory-orchestrator-cli/bin/cli.js"],
                    },
                },
            ],
            "edges": [],
        }
        third_plan = build_dry_run_merge_plan(
            job=third_job,
            compact_graph=third_graph,
            parent_graph_commit_id="",
            historical_decision_frames=store.list_decision_frames(repo_id=first_stored["repo_id"], exclude_job_id=third_job["job_id"]),
        )
        intra_session_candidates = [
            candidate
            for candidate in third_plan.review_candidates
            if candidate["source_node_id"].endswith("ghi:00") and candidate["target_node_id"].endswith("jkl:00")
        ]

        assert intra_session_candidates
        assert intra_session_candidates[0]["proposed_relation"] == "DUPLICATE_OF"
    finally:
        store.close()


def test_production_fixture_embedding_coverage_counts_only_current_retrieval_docs(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.retrieval_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(settings.retrieval_db_path)
    try:
        RetrievalIndexStore(conn).replace_documents(
            [
                RetrievalDocument(
                    doc_id="doc:current:1",
                    doc_type="packet",
                    graph_node_id="node:1",
                    node_kind="Packet",
                    packet_id="WP0001",
                    commit_sha="abc123",
                    title="current",
                    body="current doc",
                ),
                RetrievalDocument(
                    doc_id="doc:current:2",
                    doc_type="packet",
                    graph_node_id="node:2",
                    node_kind="Packet",
                    packet_id="WP0002",
                    commit_sha="def456",
                    title="current 2",
                    body="current doc 2",
                ),
            ]
        )
        embeddings = GraphEmbeddingStore(conn, db_path=settings.retrieval_db_path)
        embeddings.upsert(
            GraphEmbeddingRecord.create(
                node_id="node:1",
                node_kind="Packet",
                memory_class="graph_context",
                graph_scope="v2",
                graph_path="doc:current:1",
                session_id="s-coverage",
                extraction_run_id="run",
                embedding_kind="retrieval_text",
                model="hash-fallback",
                text="current doc",
                vector=[0.1, 0.2],
            )
        )
        embeddings.upsert(
            GraphEmbeddingRecord.create(
                node_id="old-node",
                node_kind="Packet",
                memory_class="graph_context",
                graph_scope="v2",
                graph_path="doc:stale:old",
                session_id="s-coverage",
                extraction_run_id="old-run",
                embedding_kind="retrieval_text",
                model="hash-fallback",
                text="stale doc",
                vector=[0.1, 0.2],
            )
        )
    finally:
        conn.close()

    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s-coverage", boundary_event_id="raw_boundary").job
    finally:
        store.close()
    fixture = export_job_fixture(settings, job_id=job["job_id"], out_dir=tmp_path / "coverage-fixture")
    coverage = fixture["fixture"]["embedding_coverage"]

    assert coverage["total_docs"] == 2
    assert coverage["embedded_docs"] == 1
    assert coverage["status"] == "partial"


def test_semantic_judge_checks_mentions_citations_and_forbidden_claims() -> None:
    case = {
        "case_id": "why-graph-service",
        "must_mention": ["reasoning", "commit"],
        "must_cite": ["packet", "evidence"],
        "must_not_claim": ["unsupported final decision"],
    }
    good = {
        "answer": "The reasoning changed after a commit connected graph_service.py to production retrieval.",
        "citations": [{"type": "packet", "id": "WP0001"}, {"type": "evidence", "id": "raw_1"}],
    }
    bad = {"answer": "Unsupported final decision.", "citations": []}

    assert judge_semantic_case(case=case, answer_payload=good)["passed"] is True
    failed = judge_semantic_case(case=case, answer_payload=bad)
    assert failed["passed"] is False
    assert failed["blocking_failures"]


def test_production_runner_fails_instead_of_completing_empty_graph_when_no_work_packets(tmp_path: Path) -> None:
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

    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(
            session_id="s-no-commit",
            boundary_event_id="raw_boundary",
            source_app="codex",
            repo_path=str(tmp_path),
            source_evidence_day="2026-05-21",
        ).job
        runner = ProductionSessionJobRunner(settings, job_store=store)

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


def test_production_reset_requires_backup_and_preserves_raw_config_and_job_tables(tmp_path: Path) -> None:
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

    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(session_id="s1", boundary_event_id="raw_boundary").job
    finally:
        store.close()

    with pytest.raises(ValueError):
        reset_production_storage(settings, backup=False, clean_graph=True, clean_retrieval=True)
    with pytest.raises(ValueError, match="clean-graph and --clean-retrieval"):
        reset_production_storage(
            settings,
            backup=True,
            clean_graph=False,
            clean_retrieval=True,
            force_if_daemon_running=True,
        )
    with pytest.raises(ValueError, match="clean-graph and --clean-retrieval"):
        reset_production_storage(
            settings,
            backup=True,
            clean_graph=True,
            clean_retrieval=False,
            force_if_daemon_running=True,
        )

    result = reset_production_storage(
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

    store = ProductionSessionJobStore(settings)
    try:
        assert store.get_job(job["job_id"]) is not None
        marker = store.marker()
    finally:
        store.close()
    assert marker is not None
    assert marker["pipeline_version"] == PIPELINE_VERSION
    assert marker["graph_schema_version"] == GRAPH_SCHEMA_VERSION
    assert marker["cleaned"] == {"graph": True, "retrieval": True, "faiss": True}


def test_production_fresh_init_marks_empty_new_install_without_reset(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    settings.home.mkdir(parents=True, exist_ok=True)

    result = initialize_fresh_production_storage(settings)

    assert result["ok"] is True
    assert result["created"] is True
    marker = result["marker"]
    assert marker["fresh_install"] is True
    assert marker["pipeline_version"] == PIPELINE_VERSION
    assert marker["graph_schema_version"] == GRAPH_SCHEMA_VERSION
    assert marker["cleaned"] == {"graph": True, "retrieval": True, "faiss": True}
    assert marker["validated"] == {"graph_empty": True, "retrieval_empty": True}
    assert require_complete_production_marker(marker) == marker

    again = initialize_fresh_production_storage(settings)
    assert again["created"] is False
    assert again["reason"] == "marker_exists"


def test_production_fresh_init_refuses_non_empty_retrieval_store(tmp_path: Path) -> None:
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
        initialize_fresh_production_storage(settings)


def test_production_adopt_production_backs_up_and_preserves_existing_stores(tmp_path: Path) -> None:
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
        adopt_existing_production_storage(
            settings,
            backup=False,
            validate_graph=True,
            validate_retrieval=True,
            force_if_daemon_running=True,
        )
    with pytest.raises(ValueError, match="validate-graph and --validate-retrieval"):
        adopt_existing_production_storage(
            settings,
            backup=True,
            validate_graph=False,
            validate_retrieval=True,
            force_if_daemon_running=True,
        )

    result = adopt_existing_production_storage(
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

    store = ProductionSessionJobStore(settings)
    try:
        marker = store.marker()
    finally:
        store.close()
    assert marker is not None
    assert marker["adopted_existing_production"] is True
    assert marker["validated"] == {"graph": True, "retrieval": True}
    assert marker["cleaned"] == {"graph": False, "retrieval": False, "faiss": False}
    assert require_complete_production_marker(marker) == marker


def test_production_runner_rejects_missing_incomplete_or_wrong_reset_marker() -> None:
    complete_marker = {
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "cleaned": {"graph": True, "retrieval": True, "faiss": True},
    }
    adopted_marker = {
        "pipeline_version": PIPELINE_VERSION,
        "graph_schema_version": GRAPH_SCHEMA_VERSION,
        "adopted_existing_production": True,
        "validated": {"graph": True, "retrieval": True},
        "cleaned": {"graph": False, "retrieval": False, "faiss": False},
    }

    assert require_complete_production_marker(complete_marker) == complete_marker
    assert require_complete_production_marker(adopted_marker) == adopted_marker
    with pytest.raises(RuntimeError, match="missing"):
        require_complete_production_marker(None)
    with pytest.raises(RuntimeError, match="version_mismatch"):
        require_complete_production_marker({**complete_marker, "pipeline_version": "old"})
    with pytest.raises(RuntimeError, match="incomplete"):
        require_complete_production_marker({**complete_marker, "cleaned": {"graph": True, "retrieval": False}})
    with pytest.raises(RuntimeError, match="incomplete"):
        require_complete_production_marker({**adopted_marker, "validated": {"graph": True, "retrieval": False}})


def test_qwen_reasoning_prompt_uses_production_contract_module() -> None:
    prompt = build_qwen_reasoning_packet_prompt(
        {
            "packet_id": "WP0001",
            "commit": {"short_sha": "abc1234"},
            "problem_refs": [{"ref": "E00001", "excerpt": "problem"}],
            "rationale_refs": [],
            "validation_refs": [],
        }
    )

    assert QWEN_REASONING_CONTRACT_VERSION == "stage4-reset-2026-05-14"
    assert len(qwen_reasoning_contract_hash()) == 64
    assert "Support refs are provenance only" in prompt
    assert "Input packet:" in prompt


def test_central_session_graph_write_preserves_repo_id_metadata() -> None:
    store = InMemoryGraphStore()
    result = runner_module._upsert_compact_graph(
        store,
        (
            {
                "id": "reason:1",
                "kind": "ReasoningNode",
                "label": "Decision: repo scoped retrieval",
                "summary": "Repo scoped retrieval keeps memories separated.",
                "properties_json": "{}",
            },
        ),
        (),
        job={
            "job_id": "v2job:abcdefghijklmnop",
            "session_id": "session:1",
            "source_app": "codex",
            "repo_id": "repo:test",
        },
    )

    assert result["node_write_count"] == 1
    node = store.nodes["abcdefghijkl:reason:1"]
    assert node.metadata["repo_id"] == "repo:test"
    assert node.metadata["original_node_id"] == "reason:1"


def test_central_session_graph_write_degrades_kuzu_buffer_failure(tmp_path: Path) -> None:
    class FailingGraphStore(InMemoryGraphStore):
        def init_schema(self) -> None:
            raise RuntimeError("buffer pool is full")

    result = runner_module._write_curated_session_graph_to_central(
        lambda _path: FailingGraphStore(),
        tmp_path / "amo.kuzu",
        nodes=(),
        edges=(),
        job={"job_id": "v2job:test", "session_id": "session:1", "repo_id": "repo:test"},
    )

    assert result["status"] == "failed_recoverable"
    assert result["curated_manifest_still_available"] is True
    assert result["error_type"] == "RuntimeError"


def test_qwen_checkpoint_reuse_requires_same_runtime_contract(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    packet = {"packet_id": "WP0001", "commit": {"short_sha": "abc123"}, "problem_refs": []}
    contract = runner_module._qwen_contract(settings)
    packet_key = runner_module._qwen_packet_key(packet, contract=contract)
    reusable = runner_module._qwen_reusable_results(
        [
            {
                "packet_id": "WP0001",
                "commit_sha": "abc123",
                "contract_hash": contract["contract_hash"],
                "parsed_output": {"nodes": []},
            }
        ],
        existing_manifest={"packets": [packet_key]},
        packet_keys=[packet_key],
    )

    assert runner_module._qwen_packet_cache_key(packet_key) in reusable

    next_contract = {**contract, "contract_hash": "different-model-or-schema"}
    next_key = runner_module._qwen_packet_key(packet, contract=next_contract)
    blocked = runner_module._qwen_reusable_results(
        [
            {
                "packet_id": "WP0001",
                "commit_sha": "abc123",
                "contract_hash": contract["contract_hash"],
                "parsed_output": {"nodes": []},
            }
        ],
        existing_manifest={"packets": [packet_key]},
        packet_keys=[next_key],
    )

    assert blocked == {}


def test_auto_drain_closes_graph_before_production_runner_opens(
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
        def __init__(self, settings: Settings, stage_lock_factory: object | None = None) -> None:
            del settings
            assert stage_lock_factory is auto_drain_module.production_stage_lock
            events.append("runner_open")

        def run_next(self) -> dict[str, object]:
            events.append("runner_run")
            return {"ok": True, "ran": False}

        def close(self) -> None:
            events.append("runner_close")

    monkeypatch.setattr(auto_drain_module, "GraphRagService", FakeGraph)
    monkeypatch.setattr(auto_drain_module, "ProductionSessionJobRunner", FakeRunner)

    result = daemon_module._run_auto_drain_once(settings)

    assert result["records_ingested"] == 1
    assert events == ["graph_open", "drain", "graph_close", "runner_open", "runner_run", "runner_close"]


def test_session_detail_prefers_production_raw_artifact_and_skips_pending_scan(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = ProductionSessionJobStore(settings)
    try:
        session_id = "session-fast-detail"
        job = store.enqueue_session(
            session_id=session_id,
            boundary_event_id="evt-boundary",
            source_app="codex",
            repo_path=str(tmp_path),
            source_evidence_day="2026-05-27",
        ).job
        stage_dir = Path(job["artifact_dir"]) / "evidence_view"
        stage_dir.mkdir(parents=True, exist_ok=True)
        raw_path = stage_dir / "session_raw_evidence.jsonl"
        raw_path.write_text(
            "\n".join(
                [
                    json.dumps({"id": "raw-1", "session_id": session_id, "created_at": "2026-05-27T00:00:01Z", "event_name": "prompt", "payload": {"prompt": "build it"}}),
                    json.dumps({"id": "raw-2", "session_id": session_id, "created_at": "2026-05-27T00:00:02Z", "event_name": "stop", "payload": {"last_assistant_message": "done"}}),
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        view_path = stage_dir / "reasoning_evidence_view.json"
        view_path.write_text(json.dumps({"input_raw": str(raw_path), "raw_record_count": 2}), encoding="utf-8")
        store.start_stage(
            job_id=str(job["job_id"]),
            stage="evidence_view",
            input_artifact="raw",
            input_hash="raw-hash",
            stage_config_hash="config-hash",
        )
        store.complete_stage(
            job_id=str(job["job_id"]),
            stage="evidence_view",
            output_artifact=str(view_path),
            output_hash="view-hash",
            diagnostics={"raw_record_count": 2},
        )
    finally:
        store.close()

    records, source = _load_session_evidence_records(settings, session_id=session_id, limit=10)
    pending = _session_pending_summary(settings, session_id=session_id)
    fallback = build_session_detail_fallback(settings, session_id=session_id, limit=10, error=RuntimeError("locked"))

    assert source == "production_session_raw_evidence_artifact"
    assert [record["id"] for record in records] == ["raw-1", "raw-2"]
    assert pending["source"] == "production_job_state"
    assert pending["count"] == 0
    assert fallback["degraded"] is True
    assert len(fallback["timeline"]) == 2
    assert fallback["graph"]["nodes"] == []
