from __future__ import annotations

import json
import subprocess
from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.application.pipeline.job_runner import ProductionSessionJobRunner
from agent_memory_orchestrator.domain.evidence.views import build_reasoning_evidence_view
from agent_memory_orchestrator.domain.evidence.views import write_reasoning_evidence_view_artifacts
from agent_memory_orchestrator.infrastructure.sqlite.production_job_store import ProductionSessionJobStore


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


def test_production_runner_uses_nested_repo_that_owns_session_commits(tmp_path: Path) -> None:
    settings, parent, nested, full_sha = _nested_repo_session(tmp_path, session_id="s-nested")

    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(
            session_id="s-nested",
            boundary_event_id="raw_boundary",
            source_app="codex",
            repo_path=str(parent),
            source_evidence_day="2026-05-24",
        ).job
        runner = ProductionSessionJobRunner(settings, job_store=store)

        evidence = runner.run_next()
        packets = runner.run_next()

        assert evidence["stage"] == "evidence_view"
        assert packets["stage"] == "work_packets"
        assert packets["status"] == "pending"

        updated = store.get_job(job["job_id"])
        assert updated is not None
        assert Path(updated["repo_path"]).resolve() == nested.resolve()
        evidence_stage = store.stage_row(job_id=job["job_id"], stage="evidence_view")
        assert evidence_stage is not None
        assert Path(evidence_stage["diagnostics"]["repo_resolution"]["repo_root"]).resolve() == nested.resolve()
        work_stage = store.stage_row(job_id=job["job_id"], stage="work_packets")
        assert work_stage is not None
        assert work_stage["diagnostics"]["quality"]["packet_count"] == 1
        work_packets = json.loads(Path(work_stage["output_artifact"]).read_text(encoding="utf-8"))
        assert work_packets[0]["commit"]["full_sha"] == full_sha
        assert work_packets[0]["problem_refs"]
        assert work_packets[0]["rationale_refs"]
    finally:
        store.close()


def test_production_work_packets_repairs_existing_bad_evidence_view_repo_scope(tmp_path: Path) -> None:
    settings, parent, nested, full_sha = _nested_repo_session(tmp_path, session_id="s-existing")
    raw_records = list(_read_evidence_records(settings.evidence_dir / "2026-05-24.jsonl"))
    transcript = Path(raw_records[0]["payload"]["transcript_path"])

    store = ProductionSessionJobStore(settings)
    try:
        job = store.enqueue_session(
            session_id="s-existing",
            boundary_event_id="raw_boundary",
            source_app="codex",
            repo_path=str(parent),
            source_evidence_day="2026-05-24",
        ).job
        artifact_dir = Path(job["artifact_dir"])
        evidence_dir = artifact_dir / "evidence_view"
        evidence_dir.mkdir(parents=True)
        session_jsonl = evidence_dir / "session_raw_evidence.jsonl"
        session_jsonl.write_text("".join(json.dumps(row) + "\n" for row in raw_records), encoding="utf-8")
        bad_build = build_reasoning_evidence_view(session_jsonl, transcript_path=transcript, repo_root=parent)
        assert bad_build.view["commit_facts"][0]["git_truth"]["resolved"] is False
        write_reasoning_evidence_view_artifacts(bad_build, evidence_dir)
        output = evidence_dir / "reasoning_evidence_view.json"
        store.start_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            input_artifact=str(settings.evidence_dir),
            input_hash="bad-input",
            stage_config_hash="bad-config",
        )
        store.complete_stage(
            job_id=job["job_id"],
            stage="evidence_view",
            output_artifact=str(output),
            output_hash="bad-output",
            diagnostics={"quality": bad_build.quality},
        )

        runner = ProductionSessionJobRunner(settings, job_store=store)
        packets = runner.run_next()

        assert packets["stage"] == "work_packets"
        assert packets["status"] == "pending"
        updated = store.get_job(job["job_id"])
        assert updated is not None
        assert Path(updated["repo_path"]).resolve() == nested.resolve()
        work_stage = store.stage_row(job_id=job["job_id"], stage="work_packets")
        assert work_stage is not None
        assert work_stage["diagnostics"]["repo_resolution"]["repaired_commit_truth_count"] == 1
        work_packets = json.loads(Path(work_stage["output_artifact"]).read_text(encoding="utf-8"))
        assert work_packets[0]["commit"]["full_sha"] == full_sha
    finally:
        store.close()


def _nested_repo_session(tmp_path: Path, *, session_id: str) -> tuple[Settings, Path, Path, str]:
    settings = make_settings(tmp_path)
    parent = tmp_path / "Dora"
    nested = parent / "agent-memory-orchestrator"
    parent.mkdir()
    nested.mkdir()
    _init_repo(parent)
    _init_repo(nested)
    (parent / "dora.txt").write_text("dora\n", encoding="utf-8")
    _git(parent, "add", "dora.txt")
    _git(parent, "commit", "-m", "parent commit")
    (nested / "README.md").write_text("amo\n", encoding="utf-8")
    _git(nested, "add", "README.md")
    commit_output = _git(nested, "commit", "-m", "feat: nested amo memory").stdout
    full_sha = _git(nested, "rev-parse", "HEAD").stdout.strip()

    transcript = tmp_path / f"{session_id}-transcript.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "type": "response_item",
                    "timestamp": "2026-05-24T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"text": "Implement nested AMO memory graph support."}],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-24T00:01:00Z",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"text": "I will fix the repo scoping because commit-backed production graph creation needs the actual Git root."}],
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-24T00:02:00Z",
                    "payload": {
                        "type": "function_call",
                        "call_id": "call_commit",
                        "name": "shell_command",
                        "arguments": json.dumps({"command": "git commit -m nested", "workdir": str(nested)}),
                    },
                },
                {
                    "type": "response_item",
                    "timestamp": "2026-05-24T00:03:00Z",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": "call_commit",
                        "output": commit_output,
                    },
                },
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    settings.evidence_dir.mkdir(parents=True)
    (settings.evidence_dir / "2026-05-24.jsonl").write_text(
        json.dumps(
            {
                "id": "raw_start",
                "session_id": session_id,
                "source_app": "codex",
                "event_name": "session_start",
                "payload": {"cwd": str(parent), "transcript_path": str(transcript)},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return settings, parent, nested, full_sha


def _read_evidence_records(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _init_repo(path: Path) -> None:
    _git(path, "init")
    _git(path, "config", "user.name", "test")
    _git(path, "config", "user.email", "test@example.com")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr
    return result

