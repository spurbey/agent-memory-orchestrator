from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_projection_documents
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import search_projection_documents
from agent_memory_orchestrator.domain.semantic_harness import search_projection_documents_vector


def test_vector_retrieval_finds_identifier_variant_when_lexical_does_not() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''def sign_in_user():\n    """Sign in user before redirect handling."""\n    return True\n''',
            ),
        ),
    )
    docs = build_projection_documents(graph)

    lexical_hits = search_projection_documents(docs, "signin")
    vector_hits = search_projection_documents_vector(docs, "signin")

    assert lexical_hits == ()
    assert vector_hits
    assert vector_hits[0].document.title == "Symbol sign_in_user"
    assert vector_hits[0].document.source_node_id in {node.id for node in graph.nodes}
    assert vector_hits[0].embedding_method == "hash_token_char_cosine_v1"
    assert any(feature.startswith("ngram:") for feature in vector_hits[0].matched_features)


def test_vector_retrieval_returns_no_hits_for_unmatched_query() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/main.py", text="def run():\n    return True\n"),),
    )

    hits = search_projection_documents_vector(build_projection_documents(graph), "billing invoice webhook")

    assert hits == ()
