from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.evidence.drain import EvidenceDrain
from agent_memory_orchestrator.evidence.drain import _read_jsonl_from
from agent_memory_orchestrator.graph.store import InMemoryGraphStore
from agent_memory_orchestrator.evidence.raw_store import RawEvidenceStore
from agent_memory_orchestrator.graph.session import DeterministicGraphExtractor, SessionGraphBuilder
from agent_memory_orchestrator.versioning import GitSnapshot
from agent_memory_orchestrator.reasoning_graph.jobs import ProductionSessionJobStore


class _StaticGitBackend:
    def __init__(self, snapshot: GitSnapshot | None = None) -> None:
        self._snapshot = snapshot or GitSnapshot(available=False)

    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return self._snapshot


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
    store = InMemoryGraphStore()
    backend = _StaticGitBackend()
    RawEvidenceStore(settings.evidence_dir).append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "read docs only"},
        session_id="s1",
        source_app="codex",
        event_name="user_prompt_submit",
    )
    drain = _drain(settings, store, backend)

    first = drain.drain()
    second = drain.drain()

    assert first["records_ingested"] == 1
    assert first["windows_processed"] == 0
    assert second["records_seen"] == 0
    assert store.list_nodes(kinds=["Prompt"], session_id="s1")
    assert not store.list_nodes(kinds=["WorkChange"], session_id="s1")


def test_drain_skips_and_quarantines_malformed_jsonl_lines(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend()
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

    first = _drain(settings, store, backend).drain()
    second = _drain(settings, store, backend).drain()

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

    first = _production_drain(settings).drain(limit=2)
    assert first["windows_processed"] == 0
    assert first["pending_sessions"] == 1

    _append_session_start(evidence, "s2")

    second = _production_drain(settings).drain(limit=1)

    assert second["windows_processed"] == 1
    assert second["triggered"][0]["session_id"] == "s1"
    assert second["triggered"][0]["trigger"]["trigger_type"] == "session_boundary"
    assert second["triggered"][0]["result"]["mode"] == "v2_job_enqueue"
    assert second["triggered"][0]["result"]["created"] is True
    assert second["pending_sessions"] == 1
    job_store = ProductionSessionJobStore(settings)
    try:
        job = job_store.get_job_by_session(session_id="s1")
    finally:
        job_store.close()
    assert job is not None
    assert job["status"] == "pending"
    assert job["current_stage"] == "evidence_view"
    assert job["source_app"] == "codex"


def test_drain_write_window_builds_context_snapshot_and_work_change(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend()
    evidence = RawEvidenceStore(settings.evidence_dir)
    _append_session_start(evidence, "s1")
    evidence.append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "implement capture-only hooks"},
        session_id="s1",
        source_app="codex",
        event_name="user_prompt_submit",
    )
    evidence.append(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "tool": "apply_patch",
            "content": "Decision: capture only hooks. Updated src/agent_memory_orchestrator/hook.py",
        },
        session_id="s1",
        source_app="codex",
        event_name="post_tool_use",
    )
    _append_session_start(evidence, "s2")
    drain = _drain(settings, store, backend)

    result = drain.drain()

    assert result["windows_processed"] == 1
    context = store.list_nodes(kinds=["ContextSnapshot"], session_id="s1")
    work = store.list_nodes(kinds=["WorkChange"], session_id="s1")
    windows = store.list_nodes(kinds=["CleanedEvidenceWindow"], session_id="s1")
    deltas = store.list_nodes(kinds=["GraphDelta"], session_id="s1")
    decisions = store.list_nodes(kinds=["Decision"], session_id="s1")
    edges = store.list_edges(session_id="s1", limit=50)
    assert context
    assert work
    assert windows
    assert deltas
    assert decisions
    assert "hook.py" in json.dumps(context[0]["metadata"])
    assert "cleaned_evidence" in windows[0]["metadata"]
    assert {edge["kind"] for edge in edges} >= {"CLEANED_INTO", "EXTRACTED_AS", "CREATED", "MODIFIES", "IMPLEMENTS"}


def test_drain_git_commit_links_work_change_when_commit_sha_is_in_closed_session(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend(
        GitSnapshot(
            available=True,
            repo_root=str(tmp_path),
            branch="main",
            head="abcdef1234567890abcdef1234567890abcdef12",
        )
    )
    evidence = RawEvidenceStore(settings.evidence_dir)
    _append_session_start(evidence, "s1")
    evidence.append(
        {
            "hook_event_name": "PostToolUse",
            "session_id": "s1",
            "content": "git commit abcdef1234567890abcdef1234567890abcdef12 -m graph ledger",
        },
        session_id="s1",
        source_app="codex",
        event_name="post_tool_use",
    )
    _append_session_start(evidence, "s2")
    drain = _drain(settings, store, backend)

    result = drain.drain()

    commits = store.list_nodes(kinds=["GitCommit"], session_id="s1")
    assert result["windows_processed"] == 1
    assert commits
    assert commits[0]["commit_id"] == "abcdef1234567890abcdef1234567890abcdef12"


def test_basic_event_git_metadata_is_compact(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend(
        GitSnapshot(
            available=True,
            repo_root=str(tmp_path),
            branch="main",
            head="abcdef1234567890abcdef1234567890abcdef12",
            status_porcelain="M huge.py\n?? generated.bin",
            changed_files=["huge.py"],
        )
    )
    RawEvidenceStore(settings.evidence_dir).append(
        {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "prompt": "read only"},
        session_id="s1",
        source_app="codex",
        event_name="user_prompt_submit",
    )

    _drain(settings, store, backend).drain()

    prompt = store.list_nodes(kinds=["Prompt"], session_id="s1")[0]
    git = prompt["metadata"]["git"]
    assert git["changed_count"] == 1
    assert "status_porcelain" not in git


def test_session_filtered_drain_uses_session_cursor_and_matching_limit(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend()
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

    result = _drain(settings, store, backend).drain(session_id="target", limit=10)
    cursors = json.loads((settings.home / ".state" / "evidence_cursors.json").read_text(encoding="utf-8"))

    assert result["records_ingested"] == 3
    assert result["windows_processed"] == 1
    assert store.list_nodes(kinds=["ContextSnapshot"], session_id="target")
    assert cursors
    assert all("::session::target" in key for key in cursors)


def test_drain_stops_after_max_windows(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend()
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
                "session_id": "s1",
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

    result = _drain(settings, store, backend).drain(limit=20, max_windows=1)
    second = _drain(settings, store, backend).drain(limit=20, max_windows=1)

    assert result["windows_processed"] == 1
    assert result["stopped_reason"] == "max_windows_reached"
    assert result["max_windows"] == 1
    assert second["windows_processed"] == 1


def _append_session_start(evidence: RawEvidenceStore, session_id: str) -> None:
    evidence.append(
        {"hook_event_name": "SessionStart", "session_id": session_id},
        session_id=session_id,
        source_app="codex",
        event_name="session_start",
    )


def _drain(settings: Settings, store: InMemoryGraphStore, backend: _StaticGitBackend) -> EvidenceDrain:
    return EvidenceDrain(
        settings,
        store,
        backend,
        builder=SessionGraphBuilder(
            settings,
            store,
            backend,
            extractor=DeterministicGraphExtractor(),
        ),
    )


def _production_drain(settings: Settings) -> EvidenceDrain:
    return EvidenceDrain(settings, InMemoryGraphStore(), _StaticGitBackend())
