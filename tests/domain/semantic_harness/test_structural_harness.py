from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import file_id
from agent_memory_orchestrator.domain.semantic_harness import harness_card_id
from agent_memory_orchestrator.domain.semantic_harness import normalize_file_path
from agent_memory_orchestrator.domain.semantic_harness import resolve_anchors
from agent_memory_orchestrator.domain.semantic_harness import symbol_id
from agent_memory_orchestrator.domain.semantic_harness import answer_structural_query


def test_harness_ids_are_deterministic_and_normalize_paths() -> None:
    repo_id = "repo:test"

    assert normalize_file_path(r".\src\auth\session.py") == "src/auth/session.py"
    assert file_id(repo_id, r"src\auth\session.py") == file_id(repo_id, "src/auth/session.py")
    assert symbol_id(repo_id, "src/auth/session.py", "AuthSession.refresh", "method") == (
        "symbol:repo:test:src/auth/session.py:AuthSession.refresh:method"
    )
    assert harness_card_id(repo_id, "s1", "file_context", ("b", "a")) == harness_card_id(
        repo_id, "s1", "file_context", ("a", "b")
    )


def test_bootstrap_creates_files_symbols_and_lazy_regions_without_raw_ast_nodes() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text="""
class AuthSession:
    def refresh(self):
        return True

def helper():
    return AuthSession()
""".strip(),
            ),
            SourceFile(path="README.md", text="# Setup\n\nRun the app."),
            SourceFile(path="pyproject.toml", text="[project]\nname='demo'\n"),
        ),
    )

    kinds = {node.kind for node in graph.nodes}
    labels = {node.label for node in graph.nodes}

    assert {"Repo", "File", "Symbol", "CodeRegion"}.issubset(kinds)
    assert "AuthSession" in labels
    assert "AuthSession.refresh" in labels
    assert "helper" in labels
    assert "Setup" in labels
    assert "CodeNode" not in kinds
    assert any(edge.kind == "DEFINES" for edge in graph.edges)


def test_exact_anchor_resolution_supports_files_symbols_and_partial_coverage() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text="class AuthSession:\n    def refresh(self):\n        return True\n",
            ),
        ),
    )

    anchors = resolve_anchors(
        graph,
        files=("src/auth/session.py",),
        symbols=("AuthSession.refresh", "MissingSymbol"),
    )

    assert {anchor.kind for anchor in anchors.resolved} == {"File", "Symbol"}
    assert anchors.unresolved == ("symbol:MissingSymbol",)


def test_structural_query_returns_compact_partial_structural_cards() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text="class AuthSession:\n    def refresh(self):\n        return True\n",
            ),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect token refresh behavior",
            files=("src/auth/session.py",),
            symbols=("AuthSession.refresh",),
            max_cards=2,
        ),
    )

    assert response.status == "partial_structural"
    assert len(response.cards) == 2
    assert response.cards[0].confidence <= 0.82
    assert response.cards[0].evidence
    assert any("structural_only" in warning for warning in response.warnings)
    assert response.trace["nodes"]


def test_structural_query_reports_unavailable_when_no_anchor_grounding_exists() -> None:
    graph = build_structural_graph("repo:test", (SourceFile(path="src/main.py", text="x = 1\n"),))

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="edit_plan",
            user_goal="fix signin redirect",
            files=("src/missing.py",),
        ),
    )

    assert response.status == "unavailable"
    assert response.cards == ()
    assert "unresolved_anchors:file:src/missing.py" in response.warnings
