from __future__ import annotations

from dataclasses import replace

from agent_memory_orchestrator.domain.semantic_harness import CommitHunk
from agent_memory_orchestrator.domain.semantic_harness import CommitWorkWindow
from agent_memory_orchestrator.domain.semantic_harness import HunkRange
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import apply_graph_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_commit_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.infrastructure.sqlite.semantic_harness import SQLiteHarnessGraphStore


def test_sqlite_graph_store_round_trips_bootstrap_graph(tmp_path) -> None:
    db_path = tmp_path / "harness.sqlite"
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth.py",
                text="def login(user):\n    return user.is_active\n",
            ),
            SourceFile(path="README.md", text="# Auth\n\nLogin behavior.\n"),
        ),
    )

    with SQLiteHarnessGraphStore.from_graph(db_path, graph) as store:
        assert store.node_ids() == tuple(sorted(node.id for node in graph.nodes))
        assert store.edge_keys() == tuple(sorted((edge.source_id, edge.target_id, edge.kind) for edge in graph.edges))

    with SQLiteHarnessGraphStore(db_path, "repo:test") as reopened:
        persisted = reopened.to_graph()
        repo_edges = reopened.outgoing("repo:test", kind="CONTAINS")

        assert persisted.repo_id == graph.repo_id
        assert {node.id for node in persisted.nodes} == {node.id for node in graph.nodes}
        assert {(edge.source_id, edge.target_id, edge.kind) for edge in persisted.edges} == {
            (edge.source_id, edge.target_id, edge.kind) for edge in graph.edges
        }
        assert repo_edges
        assert {edge.kind for edge in repo_edges} == {"CONTAINS"}


def test_sqlite_graph_store_preserves_replace_semantics(tmp_path) -> None:
    db_path = tmp_path / "harness.sqlite"
    graph = build_structural_graph("repo:test", (SourceFile(path="src/main.py", text="value = 1\n"),))
    node = next(item for item in graph.nodes if item.kind == "File")

    with SQLiteHarnessGraphStore.from_graph(db_path, graph) as store:
        assert store.upsert_node(node) is False
        store.replace_node(replace(node, summary="updated summary", metadata={**node.metadata, "reviewed": True}))

    with SQLiteHarnessGraphStore(db_path, "repo:test") as reopened:
        persisted = reopened.get_node(node.id)

        assert persisted is not None
        assert persisted.summary == "updated summary"
        assert persisted.metadata["reviewed"] is True


def test_sqlite_graph_store_applies_commit_delta_and_reopens(tmp_path) -> None:
    db_path = tmp_path / "harness.sqlite"
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (SourceFile(path="src/auth.py", text="def login():\n    return True\n"),),
    )
    delta = build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id="session-1",
            commit_sha="abc123456789",
            commit_message="fix login",
            hunks=(
                CommitHunk(
                    file_path="src/auth.py",
                    old_range=HunkRange(start=2, count=1),
                    new_range=HunkRange(start=2, count=1),
                ),
            ),
        ),
    )

    with SQLiteHarnessGraphStore.from_graph(db_path, graph) as store:
        first = apply_graph_update_delta(store, delta)
        second = apply_graph_update_delta(store, delta)

        assert first.status == "applied"
        assert second.status == "noop"

    with SQLiteHarnessGraphStore(db_path, repo_id) as reopened:
        persisted = reopened.to_graph()

        assert delta.commit_id in reopened.node_ids()
        assert {node.id for node in delta.created_nodes}.issubset({node.id for node in persisted.nodes})
        assert {(edge.source_id, edge.target_id, edge.kind) for edge in delta.created_edges}.issubset(
            {(edge.source_id, edge.target_id, edge.kind) for edge in persisted.edges}
        )
