from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.config import Settings
from agent_memory_orchestrator.graph_merge import CommitMergeEngine
from agent_memory_orchestrator.graph_store import GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.versioning import GitCommitDetails, GitDiffSummary, GitSnapshot


class _StaticVersionBackend:
    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return GitSnapshot(available=True, repo_root=str(cwd or "."), branch="main", head="abc123")

    def commit_details(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitCommitDetails:
        return GitCommitDetails(available=True, commit="abc123", subject="fix graph cleaning merge")

    def diff_summary(self, commit: str = "HEAD", cwd: str | Path | None = None) -> GitDiffSummary:
        return GitDiffSummary(
            available=True,
            base="base123",
            target="abc123",
            changed_files=["src/agent_memory_orchestrator/session_graph.py"],
            insertions=10,
            deletions=2,
            summary="1 file changed, +10/-2",
        )

    def patch_id(self, commit: str = "HEAD", cwd: str | Path | None = None) -> str:
        return "patch123"


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
        qwen_runtime="ollama",
    )


def test_commit_merge_promotes_answer_grade_nodes_and_skips_support(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="work:s1:1",
            kind="WorkChange",
            label="Clean raw artifacts before graph extraction",
            summary="Clean raw artifacts before graph extraction in session graph windows.",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw1",
            metadata={"changed_files": ["src/agent_memory_orchestrator/session_graph.py"]},
        )
    )
    store.upsert_node(
        GraphNode(
            id="evidence:raw1",
            kind="RawEvidenceRef",
            label="raw1",
            summary="post_tool_use raw evidence from codex",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw1",
        )
    )
    engine = CommitMergeEngine(make_settings(tmp_path), store, _StaticVersionBackend())

    preview = engine.finalize_session(session_id="s1", commit="HEAD", apply=False)
    applied = engine.finalize_session(session_id="s1", commit="HEAD", apply=True)

    assert preview["promoted_count"] == 0
    assert [node["id"] for node in preview["planned_promotions"]] == ["work:s1:1"]
    assert preview["skipped_support_count"] == 1
    assert applied["promoted_count"] == 1
    promoted = store.nodes["work:s1:1"]
    assert promoted.status == "committed"
    assert promoted.scope == "central"
    assert promoted.commit_id == "abc123"
    assert "edge:work:s1:1:COMMITTED_AS:commit:abc123" in store.edges
    assert "edge:work:s1:1:MODIFIES:file:src/agent_memory_orchestrator/session_graph.py" in store.edges


def test_commit_merge_creates_duplicate_relation_to_existing_central_node(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    summary = "Use commit merge engine for central graph versioning."
    store.upsert_node(
        GraphNode(
            id="decision:old",
            kind="Decision",
            label=summary,
            summary=summary,
            status="committed",
            scope="central",
            session_id="old",
            commit_id="old123",
        )
    )
    store.upsert_node(
        GraphNode(
            id="decision:s1:1",
            kind="Decision",
            label=summary,
            summary=summary,
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw2",
        )
    )
    engine = CommitMergeEngine(make_settings(tmp_path), store, _StaticVersionBackend())

    result = engine.finalize_session(session_id="s1", commit="HEAD", apply=True)

    assert result["relations"][0]["relation"] == "DUPLICATE_OF"
    assert "edge:decision:s1:1:DUPLICATE_OF:decision:old" in store.edges


def test_commit_merge_supersedes_old_central_node_without_deleting_it(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="decision:old",
            kind="Decision",
            label="Use qwen3 1.7b for graph extraction smoke mode",
            summary="Use qwen3 1.7b for graph extraction smoke mode.",
            status="committed",
            scope="central",
            session_id="old",
            commit_id="old123",
            metadata={"changed_files": ["src/agent_memory_orchestrator/session_graph.py"]},
        )
    )
    store.upsert_node(
        GraphNode(
            id="decision:s1:replace",
            kind="Decision",
            label="Replace qwen3 1.7b with qwen3 0.6b for graph extraction smoke mode",
            summary="Replace qwen3 1.7b with qwen3 0.6b for graph extraction smoke mode.",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw3",
            metadata={"changed_files": ["src/agent_memory_orchestrator/session_graph.py"]},
        )
    )
    engine = CommitMergeEngine(make_settings(tmp_path), store, _StaticVersionBackend())

    result = engine.finalize_session(session_id="s1", commit="HEAD", apply=True)

    assert result["relations"][0]["relation"] == "SUPERSEDES"
    assert store.nodes["decision:old"].status == "superseded"
    assert "decision:old" in store.nodes
    assert "edge:decision:s1:replace:SUPERSEDES:decision:old" in store.edges


def test_commit_merge_skips_command_and_snippet_noise(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    noisy_rows = [
        ("work:s1:git", "WorkChange", "Git command executed: git status --short"),
        ("decision:s1:patch", "Decision", "Apply patch"),
        ("test:s1:passed", "TestRun", 'test_run "all checks passed!", "all checks passed!",'),
        (
            "work:s1:passed-edit",
            "WorkChange",
            '"all checks passed!",; except OSError: Code edit applied to: src/agent_memory_orchestrator/evidence_window.py',
        ),
        (
            "bug:s1:snippet",
            "Bug",
            'work_note timings["compression_ms"] = _elapsed_ms(compression_started) | qwen_status["compression_fallback"] = True',
        ),
    ]
    for node_id, kind, summary in noisy_rows:
        store.upsert_node(
            GraphNode(
                id=node_id,
                kind=kind,
                label=summary[:120],
                summary=summary,
                status="draft",
                scope="session",
                session_id="s1",
                evidence_id="raw-noise",
            )
        )
    store.upsert_node(
        GraphNode(
            id="decision:s1:durable",
            kind="Decision",
            label="Use commit merge finalization for central graph versioning",
            summary="Use commit merge finalization for central graph versioning with non-destructive version edges.",
            status="draft",
            scope="session",
            session_id="s1",
            evidence_id="raw-good",
        )
    )
    engine = CommitMergeEngine(make_settings(tmp_path), store, _StaticVersionBackend())

    result = engine.finalize_session(session_id="s1", commit="HEAD", apply=False)

    assert [node["id"] for node in result["planned_promotions"]] == ["decision:s1:durable"]
    assert result["skipped_answer_count"] == 5
