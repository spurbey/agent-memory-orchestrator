from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import CommitHunk
from agent_memory_orchestrator.domain.semantic_harness import CommitWorkWindow
from agent_memory_orchestrator.domain.semantic_harness import HunkRange
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_commit_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import commit_id
from agent_memory_orchestrator.domain.semantic_harness import file_id
from agent_memory_orchestrator.domain.semantic_harness import symbol_id
from agent_memory_orchestrator.domain.semantic_harness import version_id


def test_commit_update_delta_creates_commit_hunk_and_versions_for_mapped_symbol() -> None:
    repo_id = "repo:test"
    sha = "abc123456789"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth.py",
                text="def login():\n    return True\n",
            ),
        ),
    )

    delta = build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id="session-1",
            commit_sha=sha,
            commit_message="fix login flow",
            hunks=(
                CommitHunk(
                    file_path="src/auth.py",
                    old_range=HunkRange(start=2, count=1),
                    new_range=HunkRange(start=2, count=1),
                    text="-    return False\n+    return True",
                ),
            ),
        ),
    )

    node_kinds = {node.kind for node in delta.created_nodes}
    edge_kinds = {edge.kind for edge in delta.created_edges}
    file_node_id = file_id(repo_id, "src/auth.py")
    login_id = symbol_id(repo_id, "src/auth.py", "login", "function")

    assert {"WorkWindow", "Commit", "Hunk", "FileVersion", "SymbolVersion"}.issubset(node_kinds)
    assert {"DERIVED_FROM_WORK_WINDOW", "VERSION_OF", "CHANGED_IN", "MAPS_TO_SYMBOL"}.issubset(edge_kinds)
    assert delta.commit_id == commit_id(repo_id, sha)
    assert version_id(file_node_id, sha) in {node.id for node in delta.created_nodes}
    assert version_id(login_id, sha) in {node.id for node in delta.created_nodes}
    assert delta.hunk_mappings[0].status == "mapped"
    assert delta.semantic_review == {"accepted": 0, "review_only": 0, "rejected": 0, "quarantined": 0}
    assert delta.projection_refresh_required is True
    assert delta.as_dict()["created_nodes"]


def test_commit_update_delta_keeps_review_only_mappings_from_creating_symbol_versions() -> None:
    repo_id = "repo:test"
    sha = "def456789abc"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth.py",
                text=(
                    "def login():\n"
                    "    return True\n"
                    "\n"
                    "def logout():\n"
                    "    return True\n"
                ),
            ),
        ),
    )

    delta = build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id="session-1",
            commit_sha=sha,
            commit_message="touch auth flows",
            hunks=(
                CommitHunk(
                    file_path="src/auth.py",
                    old_range=HunkRange(start=1, count=4),
                    new_range=HunkRange(start=1, count=4),
                ),
            ),
        ),
    )

    assert {mapping.status for mapping in delta.hunk_mappings} == {"review_only"}
    assert "FileVersion" in {node.kind for node in delta.created_nodes}
    assert "SymbolVersion" not in {node.kind for node in delta.created_nodes}
    assert all(
        edge.metadata.get("mapping_status") == "review_only"
        for edge in delta.created_edges
        if edge.kind == "MAPS_TO_SYMBOL"
    )
    assert "RelationOccurrence" not in {node.kind for node in delta.created_nodes}
    assert "CO_CHANGED_WITH" not in {edge.kind for edge in delta.created_edges}


def test_commit_update_delta_seeds_cochange_occurrence_for_multiple_mapped_entities() -> None:
    repo_id = "repo:test"
    sha = "abcabc123123"
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

    delta = build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id=repo_id,
            session_id="session-1",
            commit_sha=sha,
            commit_message="update auth flows",
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

    occurrence_nodes = [node for node in delta.created_nodes if node.kind == "RelationOccurrence"]
    cochange_edges = [edge for edge in delta.created_edges if edge.kind == "CO_CHANGED_WITH"]

    assert len(occurrence_nodes) == 1
    assert len(delta.created_relation_occurrences) == 1
    assert len(delta.updated_edge_weights) == 1
    assert len(cochange_edges) == 1
    assert occurrence_nodes[0].metadata["relation_kind"] == "CO_CHANGED_WITH"
    assert occurrence_nodes[0].metadata["reason_status"] == "semantic_pending"
    assert occurrence_nodes[0].metadata["commit_message"] == "update auth flows"
    assert cochange_edges[0].metadata["occurrence_id"] == occurrence_nodes[0].id
