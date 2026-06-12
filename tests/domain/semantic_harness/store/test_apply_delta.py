from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import CommitHunk
from agent_memory_orchestrator.domain.semantic_harness import CommitWorkWindow
from agent_memory_orchestrator.domain.semantic_harness import GraphUpdateDelta
from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import HunkRange
from agent_memory_orchestrator.domain.semantic_harness import InMemoryHarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import apply_graph_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_commit_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import symbol_id
from agent_memory_orchestrator.domain.semantic_harness import version_id


def test_apply_delta_adds_commit_versions_and_edges_idempotently() -> None:
    repo_id = "repo:test"
    sha = "abc123456789"
    graph = build_structural_graph(
        repo_id,
        (SourceFile(path="src/auth.py", text="def login():\n    return True\n"),),
    )
    delta = build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id="session-1",
            commit_sha=sha,
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
    store = InMemoryHarnessGraphStore.from_graph(graph)

    first = apply_graph_update_delta(store, delta)
    second = apply_graph_update_delta(store, delta)
    updated = store.to_graph()

    login_version_id = version_id(symbol_id(repo_id, "src/auth.py", "login", "function"), sha)
    assert first.status == "applied"
    assert first.created_node_ids
    assert first.created_edge_keys
    assert second.status == "noop"
    assert set(second.skipped_node_ids) == {node.id for node in delta.created_nodes}
    assert set(second.skipped_edge_keys) == {(edge.source_id, edge.target_id, edge.kind) for edge in delta.created_edges}
    assert login_version_id in {node.id for node in updated.nodes}
    assert len(updated.nodes) == len(graph.nodes) + len(delta.created_nodes)
    assert len(updated.edges) == len(graph.edges) + len(delta.created_edges)


def test_apply_delta_updates_existing_cochange_edge_with_new_occurrence() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth.py",
                text=(
                    "def login():\n"
                    "    return True\n"
                    "\n"
                    "def refresh():\n"
                    "    return True\n"
                ),
            ),
        ),
    )
    store = InMemoryHarnessGraphStore.from_graph(graph)
    first_delta = _two_symbol_delta(graph, repo_id=repo_id, sha="aaa111")
    second_delta = _two_symbol_delta(graph, repo_id=repo_id, sha="bbb222")

    first = apply_graph_update_delta(store, first_delta)
    second = apply_graph_update_delta(store, second_delta)

    assert first.status == "applied"
    assert second.status == "applied"
    cochange_edges = [
        edge
        for edge in store.to_graph().edges
        if edge.kind == "CO_CHANGED_WITH"
    ]
    assert len(cochange_edges) == 1
    assert second.updated_edge_keys == ((cochange_edges[0].source_id, cochange_edges[0].target_id, "CO_CHANGED_WITH"),)
    assert cochange_edges[0].metadata["cochange_count"] == 2
    assert cochange_edges[0].metadata["source_changed_count"] == 2
    assert cochange_edges[0].metadata["target_changed_count"] == 2
    assert cochange_edges[0].metadata["either_changed_count"] == 2
    assert cochange_edges[0].metadata["jaccard"] == 1.0
    assert cochange_edges[0].metadata["score_components"] == {
        "cochange_jaccard": 1.0,
        "recency_score": 0.0,
        "validation_score": 0.0,
        "reason_quality_score": 0.0,
    }
    assert len(cochange_edges[0].metadata["occurrence_ids"]) == 2
    assert len(cochange_edges[0].metadata["commit_ids"]) == 2
    assert len(cochange_edges[0].metadata["occurrence_ids"]) == cochange_edges[0].metadata["cochange_count"]
    assert cochange_edges[0].metadata["cochange_count"] <= cochange_edges[0].metadata["either_changed_count"]
    assert cochange_edges[0].metadata["stored_strength"] == 0.45
    assert cochange_edges[0].metadata["stored_strength"] == cochange_edges[0].weight


def test_apply_delta_cochange_strength_uses_entity_change_denominator() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth.py",
                text=(
                    "def login():\n"
                    "    return True\n"
                    "\n"
                    "def refresh():\n"
                    "    return True\n"
                ),
            ),
        ),
    )
    store = InMemoryHarnessGraphStore.from_graph(graph)
    apply_graph_update_delta(store, _single_login_delta(graph, repo_id=repo_id, sha="aaa111"))
    apply_graph_update_delta(store, _two_symbol_delta(graph, repo_id=repo_id, sha="bbb222"))

    cochange_edge = next(edge for edge in store.to_graph().edges if edge.kind == "CO_CHANGED_WITH")

    assert cochange_edge.metadata["cochange_count"] == 1
    assert cochange_edge.metadata["source_changed_count"] == 2
    assert cochange_edge.metadata["target_changed_count"] == 1
    assert cochange_edge.metadata["either_changed_count"] == 2
    assert cochange_edge.metadata["jaccard"] == 0.5
    assert cochange_edge.metadata["stored_strength"] == 0.23
    assert cochange_edge.metadata["score_components"]["cochange_jaccard"] == 0.5
    assert cochange_edge.metadata["score_components"]["reason_quality_score"] == 0.0


def test_apply_delta_fails_before_write_when_edge_endpoint_is_missing() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(repo_id, (SourceFile(path="src/main.py", text="value = 1\n"),))
    store = InMemoryHarnessGraphStore.from_graph(graph)
    before_node_count = len(store.node_ids())
    before_edge_count = len(store.edge_keys())
    delta = GraphUpdateDelta(
        delta_id="delta:bad",
        repo_id=repo_id,
        work_window_id="work:bad",
        commit_id="commit:bad",
        created_nodes=(
            HarnessNode(id="node:new", kind="Commit", label="new", repo_id=repo_id),
        ),
        created_edges=(
            HarnessEdge(source_id="node:new", target_id="node:missing", kind="CHANGED_IN"),
        ),
        hunk_mappings=(),
        semantic_review={"accepted": 0, "review_only": 0, "rejected": 0, "quarantined": 0},
    )

    result = apply_graph_update_delta(store, delta)

    assert result.status == "failed"
    assert result.applied is False
    assert result.created_node_ids == ()
    assert result.missing_node_ids == ("node:missing",)
    assert "missing_edge_endpoints" in result.failure_reasons
    assert len(store.node_ids()) == before_node_count
    assert len(store.edge_keys()) == before_edge_count


def test_apply_delta_rejects_repo_mismatch_before_write() -> None:
    graph = build_structural_graph("repo:a", (SourceFile(path="src/main.py", text="value = 1\n"),))
    store = InMemoryHarnessGraphStore.from_graph(graph)
    delta = GraphUpdateDelta(
        delta_id="delta:mismatch",
        repo_id="repo:b",
        work_window_id="work:mismatch",
        commit_id="commit:mismatch",
        created_nodes=(),
        created_edges=(),
        hunk_mappings=(),
        semantic_review={"accepted": 0, "review_only": 0, "rejected": 0, "quarantined": 0},
    )

    result = apply_graph_update_delta(store, delta)

    assert result.status == "failed"
    assert result.failure_reasons == ("repo_id_mismatch:repo:a!=repo:b",)


def _two_symbol_delta(graph, *, repo_id: str, sha: str):
    return build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id=f"session-{sha}",
            commit_sha=sha,
            commit_message="update auth pair",
            hunks=(
                CommitHunk(
                    file_path="src/auth.py",
                    old_range=HunkRange(start=2, count=1),
                    new_range=HunkRange(start=2, count=1),
                ),
                CommitHunk(
                    file_path="src/auth.py",
                    old_range=HunkRange(start=5, count=1),
                    new_range=HunkRange(start=5, count=1),
                ),
            ),
        ),
    )


def _single_login_delta(graph, *, repo_id: str, sha: str):
    return build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id=f"session-{sha}",
            commit_sha=sha,
            commit_message="update login only",
            hunks=(
                CommitHunk(
                    file_path="src/auth.py",
                    old_range=HunkRange(start=2, count=1),
                    new_range=HunkRange(start=2, count=1),
                ),
            ),
        ),
    )
