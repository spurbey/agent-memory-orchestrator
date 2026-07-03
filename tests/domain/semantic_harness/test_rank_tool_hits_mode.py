from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_projection_documents
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_rank_tool_hits


def test_rank_tool_hits_uses_candidate_local_projection_docs() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/snapshots.py",
                text=(
                    "def raw_edge_count():\n"
                    "    edge_count = len(snapshot_edges)\n"
                    "    edge_count_text = str(edge_count)\n"
                    "    return edge_count_text\n"
                ),
            ),
            SourceFile(
                path="src/sqlite_store.py",
                text=(
                    "def persist_unique_edges():\n"
                    "    \"\"\"SQLite persistence stores unique graph edge keys with a primary key dedupe.\"\"\"\n"
                    "    return True\n"
                ),
            ),
        ),
    )
    docs = build_projection_documents(graph)

    result = answer_rank_tool_hits(
        graph,
        user_goal="which file explains persisted SQLite primary key dedupe?",
        recent_tool_result={
            "kind": "rg",
            "user_prompt": "explain SQLite persisted primary key dedupe",
            "text": "\n".join(
                (
                    "src/snapshots.py:2:    edge_count = len(snapshot_edges)",
                    "src/snapshots.py:3:    edge_count_text = str(edge_count)",
                    "src/snapshots.py:4:    return edge_count_text",
                    "src/sqlite_store.py:2:    \"\"\"SQLite persistence stores unique graph edge keys with a primary key dedupe.\"\"\"",
                )
            ),
        },
        projection_documents=docs,
    )

    assert result.status == "ready"
    assert result.ranked_hits[0].path == "src/sqlite_store.py"
    assert result.ranked_hits[0].semantic_similarity > result.ranked_hits[1].semantic_similarity
    assert any(
        reason.startswith("candidate_local_semantic_similarity:")
        for reason in result.ranked_hits[0].reason_codes
    )
    assert result.warnings == ("candidate_discovery_only", "embedding_backend:hash_fallback")


def test_rank_tool_hits_never_adds_candidates_outside_raw_tool_output() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(path="src/visible.py", text="def run():\n    return True\n"),
            SourceFile(
                path="src/hidden_match.py",
                text='def persist_unique_edges():\n    """SQLite edge count primary key dedupe."""\n    return True\n',
            ),
        ),
    )

    result = answer_rank_tool_hits(
        graph,
        user_goal="SQLite edge count primary key dedupe",
        recent_tool_result={"kind": "rg", "text": "src/visible.py:1:def run():"},
        projection_documents=build_projection_documents(graph),
    )

    assert [hit.path for hit in result.ranked_hits] == ["src/visible.py"]


def test_rank_tool_hits_returns_unavailable_without_rg_rows() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/main.py", text="def run():\n    return True\n"),),
    )

    result = answer_rank_tool_hits(
        graph,
        user_goal="find run",
        recent_tool_result={"kind": "rg", "text": "no matches"},
    )

    assert result.status == "unavailable"
    assert result.ranked_hits == ()
    assert result.warnings == ("no_rankable_tool_lines",)
