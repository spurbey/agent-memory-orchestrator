from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_projection_documents
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph


def test_projection_documents_include_only_high_signal_bootstrap_nodes() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''"""Auth module."""\n\nclass AuthSession:\n    def refresh(self):\n        """Refresh tokens before redirect checks."""\n        return True\n''',
            ),
            SourceFile(
                path="README.md",
                text="# Auth Flow\n\nUse src/auth/session.py and AuthSession.refresh for token renewal.\n",
            ),
        ),
    )

    docs = build_projection_documents(graph)
    source_kinds = {doc.source_kind for doc in docs}
    doc_types = {doc.doc_type for doc in docs}

    assert {"File", "Symbol", "DocSection", "DocString"}.issubset(source_kinds)
    assert {"file_summary", "symbol_summary", "doc_semantic_summary"}.issubset(doc_types)
    assert "FileVersion" not in source_kinds
    assert "SymbolVersion" not in source_kinds
    assert "RelationOccurrence" not in source_kinds
    assert all(doc.text.strip() for doc in docs)


def test_projection_documents_preserve_doc_semantic_content_for_discovery() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text='''class AuthSession:\n    def refresh(self):\n        """Refresh tokens before redirect checks."""\n        return True\n''',
            ),
            SourceFile(path="README.md", text="# Auth Flow\n\nAuthSession.refresh handles token renewal.\n"),
        ),
    )

    docs = build_projection_documents(graph)
    doc_text_by_kind = {doc.source_kind: [] for doc in docs}
    for doc in docs:
        doc_text_by_kind[doc.source_kind].append(doc.text)

    assert any("AuthSession.refresh" in text for text in doc_text_by_kind["Symbol"])
    assert any("Refresh tokens before redirect checks." in text for text in doc_text_by_kind["DocString"])
    assert any("AuthSession.refresh handles token renewal" in text for text in doc_text_by_kind["DocSection"])


def test_projection_document_ids_and_hashes_are_deterministic() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="src/main.py", text='def run():\n    """Run the app."""\n    return True\n'),),
    )

    first = build_projection_documents(graph)
    second = build_projection_documents(graph)

    assert [doc.doc_id for doc in first] == [doc.doc_id for doc in second]
    assert [doc.content_hash for doc in first] == [doc.content_hash for doc in second]
    assert all(len(doc.content_hash) == 64 for doc in first)
