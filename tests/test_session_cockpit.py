from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.runtime.daemon.server import GRAPH3D_HTML, SESSION_COCKPIT_HTML
from agent_memory_orchestrator.application.services.memory_graph.service import GraphRagService
from agent_memory_orchestrator.infrastructure.kuzu import GraphEdge, GraphNode, InMemoryGraphStore
from agent_memory_orchestrator.llm.qwen import DeterministicPlanner
from agent_memory_orchestrator.versioning import GitSnapshot


class _StaticGitBackend:
    def __init__(self, snapshot: GitSnapshot | None = None) -> None:
        self._snapshot = snapshot or GitSnapshot(available=False)

    def snapshot(self, cwd: str | Path | None = None) -> GitSnapshot:
        return self._snapshot


def test_session_cockpit_exposes_timeline_and_cleaned_windows(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        svc.capture_hook(
            {
                "hook_event_name": "UserPromptSubmit",
                "session_id": "ui-s1",
                "prompt": "show cleaned session artifacts in the web ui",
            },
            default_agent="codex",
        )
        svc.capture_hook(
            {
                "hook_event_name": "PostToolUse",
                "session_id": "ui-s1",
                "tool": "apply_patch",
                "tool_response": '{"output":"Success. Updated the following files:\\nM C:\\\\repo\\\\src\\\\ui.py\\n"}',
            },
            default_agent="codex",
        )

        overview = svc.session_overview(limit=5)
        detail = svc.session_detail(session_id="ui-s1", limit=20)
    finally:
        svc.close()

    assert overview["sessions"][0]["session_id"] == "ui-s1"
    assert overview["sessions"][0]["raw_events"] == 2
    assert len(detail["timeline"]) == 2
    assert detail["windows"]
    assert detail["windows"][0]["trigger"]["trigger_type"] == "pending"
    encoded_window = str(detail["windows"][0]["cleaned_evidence"])
    assert "show cleaned session artifacts" in encoded_window
    assert "src/ui.py" in encoded_window


def test_central_graph_snapshot_lists_committed_nodes_and_edges(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="window:s1:raw1",
            kind="CleanedEvidenceWindow",
            label="write cleaned window",
            summary="Cleaned raw write evidence.",
            status="draft",
            scope="session",
            session_id="s1",
        )
    )
    store.upsert_node(
        GraphNode(
            id="reason:s1:raw1",
            kind="ReasoningNode",
            label="session reasoning",
            summary="Created reasoning from cleaned evidence.",
            status="draft",
            scope="session",
            session_id="s1",
        )
    )
    store.upsert_node(
        GraphNode(
            id="work:s1:one",
            kind="WorkChange",
            label="session work",
            summary="Implemented session cockpit UI.",
            status="draft",
            scope="session",
            session_id="s1",
        )
    )
    store.upsert_node(
        GraphNode(
            id="commit:abc123",
            kind="GitCommit",
            label="abc123",
            summary="Git commit abc123 linked to session work",
            status="committed",
            scope="central",
            session_id="s1",
            commit_id="abc123",
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:window-reason",
            source_id="window:s1:raw1",
            target_id="reason:s1:raw1",
            kind="EXTRACTED_AS",
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:reason-work",
            source_id="reason:s1:raw1",
            target_id="work:s1:one",
            kind="CREATED",
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:commit",
            source_id="work:s1:one",
            target_id="commit:abc123",
            kind="COMMITTED_AS",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        central = svc.central_graph(limit=10)
    finally:
        svc.close()

    assert [node["id"] for node in central["nodes"]] == [
        "commit:abc123",
        "work:s1:one",
        "reason:s1:raw1",
        "window:s1:raw1",
    ]
    assert {edge["kind"] for edge in central["edges"]} >= {"COMMITTED_AS", "CREATED", "EXTRACTED_AS"}


def test_central_graph_snapshot_falls_back_to_isolated_production_session_graph(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    store.upsert_node(
        GraphNode(
            id="reasoning:WP0057:decision:1",
            kind="ReasoningNode",
            label="Gate Qwen decision quality",
            summary="Qwen reasoning extraction was gated by deterministic validation.",
            status="session_final",
            scope="session",
            session_id="s1",
            commit_id="167bb3a",
            metadata={"packet_id": "WP0057", "commit_sha": "167bb3a"},
        )
    )
    store.upsert_node(
        GraphNode(
            id="commit:167bb3a",
            kind="Commit",
            label="167bb3a",
            summary="Reasoning graph commit for Qwen quality gates.",
            status="session_final",
            scope="session",
            session_id="s1",
            commit_id="167bb3a",
        )
    )
    store.upsert_node(
        GraphNode(
            id="packet:WP0057",
            kind="Packet",
            label="WP0057",
            summary="Commit-backed reasoning packet.",
            status="candidate_reasoning_packet",
            scope="session",
            session_id="s1",
            commit_id="167bb3a",
        )
    )
    store.upsert_node(
        GraphNode(
            id="code:validation.py:validate",
            kind="CodeNode",
            label="validation.py::validate",
            summary="Validation code related to reasoning-node acceptance.",
            status="session_final",
            scope="session",
            session_id="s1",
            commit_id="167bb3a",
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:packet-reasoning",
            source_id="packet:WP0057",
            target_id="reasoning:WP0057:decision:1",
            kind="HAS_REASONING_NODE",
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:reasoning-code",
            source_id="reasoning:WP0057:decision:1",
            target_id="code:validation.py:validate",
            kind="EXPLAINS_CODE",
        )
    )
    store.upsert_edge(
        GraphEdge(
            id="edge:reasoning-commit",
            source_id="reasoning:WP0057:decision:1",
            target_id="commit:167bb3a",
            kind="EXTRACTED_FROM_COMMIT",
        )
    )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        central = svc.central_graph(limit=10)
    finally:
        svc.close()

    node_ids = {node["id"] for node in central["nodes"]}
    assert node_ids >= {"reasoning:WP0057:decision:1", "packet:WP0057", "code:validation.py:validate", "commit:167bb3a"}
    assert {edge["kind"] for edge in central["edges"]} >= {
        "HAS_REASONING_NODE",
        "EXPLAINS_CODE",
        "EXTRACTED_FROM_COMMIT",
    }


def test_central_graph_full_mode_can_return_more_than_browser_slice_limit(tmp_path: Path) -> None:
    store = InMemoryGraphStore()
    for index in range(620):
        store.upsert_node(
            GraphNode(
                id=f"reasoning:WP{index:04d}:decision:1",
                kind="ReasoningNode",
                label=f"Reasoning node {index}",
                summary="Session-final reasoning node.",
                status="session_final",
                scope="session",
                session_id="s1",
                commit_id=f"{index:07x}",
            )
        )
    svc = GraphRagService(
        make_settings(tmp_path),
        store=store,
        planner=DeterministicPlanner(),
        version_backend=_StaticGitBackend(),
    )
    try:
        sliced = svc.central_graph(limit=620)
        full = svc.central_graph(limit=620, full=True)
    finally:
        svc.close()

    assert len(sliced["nodes"]) == 500
    assert sliced["full"] is False
    assert len(full["nodes"]) == 620
    assert full["full"] is True


def test_daemon_exposes_dependency_free_3d_graph_view() -> None:
    assert "AMO Control Room" in SESSION_COCKPIT_HTML
    assert "/web/amo.css" in SESSION_COCKPIT_HTML
    assert "/web/amo.js" in SESSION_COCKPIT_HTML
    assert "data-route=\"/graph\"" in SESSION_COCKPIT_HTML
    assert '<canvas id="graphCanvas"' in GRAPH3D_HTML
    assert "3D Memory Workbench" in GRAPH3D_HTML
    assert "/web/js/graph/workbench.js" in GRAPH3D_HTML
    assert "/web/css/graph-workbench.css" in GRAPH3D_HTML
    assert "Knowledge creation flow" in GRAPH3D_HTML
    assert "Causal replay" in GRAPH3D_HTML
    assert "Similarity lens" in GRAPH3D_HTML
    assert "Retrieval Trace" in GRAPH3D_HTML
    assert "provenance" in GRAPH3D_HTML
    assert "3d-force-graph" not in GRAPH3D_HTML


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
