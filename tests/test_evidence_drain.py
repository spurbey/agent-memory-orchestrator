from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.evidence_drain import EvidenceDrain
from agent_memory_orchestrator.graph_store import InMemoryGraphStore
from agent_memory_orchestrator.raw_evidence import RawEvidenceStore
from agent_memory_orchestrator.session_graph import DeterministicGraphExtractor, SessionGraphBuilder
from agent_memory_orchestrator.versioning import GitSnapshot


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


def test_drain_write_window_builds_context_snapshot_and_work_change(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    store = InMemoryGraphStore()
    backend = _StaticGitBackend()
    evidence = RawEvidenceStore(settings.evidence_dir)
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
    drain = _drain(settings, store, backend)

    result = drain.drain()

    assert result["windows_processed"] == 1
    context = store.list_nodes(kinds=["ContextSnapshot"], session_id="s1")
    work = store.list_nodes(kinds=["WorkChange"], session_id="s1")
    decisions = store.list_nodes(kinds=["Decision"], session_id="s1")
    assert context
    assert work
    assert decisions
    assert "hook.py" in json.dumps(context[0]["metadata"])


def test_drain_git_commit_links_work_change_to_commit_from_snapshot(tmp_path: Path) -> None:
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
    RawEvidenceStore(settings.evidence_dir).append(
        {"hook_event_name": "PostToolUse", "session_id": "s1", "content": "git commit -m graph ledger"},
        session_id="s1",
        source_app="codex",
        event_name="post_tool_use",
    )
    drain = _drain(settings, store, backend)

    result = drain.drain()

    commits = store.list_nodes(kinds=["GitCommit"], session_id="s1")
    assert result["windows_processed"] == 1
    assert commits
    assert commits[0]["commit_id"] == "abcdef1234567890abcdef1234567890abcdef12"


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
