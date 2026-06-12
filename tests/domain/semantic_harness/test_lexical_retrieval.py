from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_projection_documents
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import search_projection_documents


def test_lexical_retrieval_ranks_graph_grounded_symbol_for_vague_goal() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''def refresh_token():\n    """Refresh token before redirect handling."""\n    return True\n''',
            ),
            SourceFile(path="README.md", text="# Auth\n\nUse src/auth/session.py for refresh_token redirect behavior.\n"),
        ),
    )
    docs = build_projection_documents(graph)

    hits = search_projection_documents(docs, "fix redirect token refresh")

    assert hits
    assert hits[0].document.source_kind == "Symbol"
    assert hits[0].document.title == "Symbol refresh_token"
    assert {"redirect", "token", "refresh"}.issubset(set(hits[0].matched_terms))
    assert hits[0].normalized_score > 0


def test_lexical_retrieval_returns_no_hits_for_unmatched_query() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/main.py", text="def run():\n    return True\n"),),
    )

    hits = search_projection_documents(build_projection_documents(graph), "billing invoice webhook")

    assert hits == ()


def test_lexical_retrieval_uses_cross_file_call_projection_text() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(path="src/pkg/a.py", text="from .b import helper\n\ndef run():\n    return helper()\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    hits = search_projection_documents(build_projection_documents(graph), "run calls helper")

    assert hits
    assert hits[0].document.title == "Symbol run"
    assert "helper" in hits[0].matched_terms
