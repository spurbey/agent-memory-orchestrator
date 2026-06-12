from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import CommitHunk
from agent_memory_orchestrator.domain.semantic_harness import CommitWorkWindow
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HistoricalRelationPolicy
from agent_memory_orchestrator.domain.semantic_harness import HunkRange
from agent_memory_orchestrator.domain.semantic_harness import InMemoryHarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import apply_graph_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_commit_update_delta
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import doc_section_id
from agent_memory_orchestrator.domain.semantic_harness import docstring_id
from agent_memory_orchestrator.domain.semantic_harness import file_id
from agent_memory_orchestrator.domain.semantic_harness import harness_card_id
from agent_memory_orchestrator.domain.semantic_harness import normalize_file_path
from agent_memory_orchestrator.domain.semantic_harness import resolve_anchors
from agent_memory_orchestrator.domain.semantic_harness import symbol_id
from agent_memory_orchestrator.domain.semantic_harness import answer_structural_query
from agent_memory_orchestrator.domain.semantic_harness import version_id


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


def test_bootstrap_attaches_markdown_doc_sections_to_exact_file_and_symbol_mentions() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth/session.py",
                text="class AuthSession:\n    def refresh(self):\n        return True\n",
            ),
            SourceFile(
                path="README.md",
                text="# Auth Flow\n\nUse src/auth/session.py and AuthSession.refresh for token renewal.\n",
            ),
        ),
    )

    section_id = doc_section_id(repo_id, "README.md", "Auth Flow", 1)
    file_node_id = file_id(repo_id, "src/auth/session.py")
    class_node_id = symbol_id(repo_id, "src/auth/session.py", "AuthSession", "class")
    symbol_node_id = symbol_id(repo_id, "src/auth/session.py", "AuthSession.refresh", "method")
    edge_keys = {(edge.source_id, edge.target_id, edge.kind) for edge in graph.edges}
    section = graph.node_by_id()[section_id]

    assert section.kind == "DocSection"
    assert section.metadata["heading"] == "Auth Flow"
    assert (section_id, file_node_id, "MENTIONS_FILE") in edge_keys
    assert (section_id, symbol_node_id, "MENTIONS_SYMBOL") in edge_keys
    assert (section_id, class_node_id, "MENTIONS_SYMBOL") not in edge_keys


def test_bootstrap_attaches_python_docstrings_to_symbols_without_llm() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth/session.py",
                text='''"""Auth session module."""\n\nclass AuthSession:\n    def refresh(self):\n        """Refresh tokens before redirect checks."""\n        return True\n''',
            ),
        ),
    )

    module_doc_id = docstring_id(repo_id, "src/auth/session.py", "module", "module")
    method_doc_id = docstring_id(repo_id, "src/auth/session.py", "AuthSession.refresh", "method")
    file_node_id = file_id(repo_id, "src/auth/session.py")
    symbol_node_id = symbol_id(repo_id, "src/auth/session.py", "AuthSession.refresh", "method")
    edge_keys = {(edge.source_id, edge.target_id, edge.kind) for edge in graph.edges}

    assert graph.node_by_id()[module_doc_id].kind == "DocString"
    assert graph.node_by_id()[method_doc_id].summary == "Refresh tokens before redirect checks."
    assert (module_doc_id, file_node_id, "DOCUMENTS_FILE") in edge_keys
    assert (method_doc_id, symbol_node_id, "DOCUMENTS_SYMBOL") in edge_keys


def test_bootstrap_creates_baseline_version_nodes_for_structural_entities() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/auth/session.py", text="class AuthSession:\n    def refresh(self):\n        return True\n"),
            SourceFile(path="README.md", text="# Setup\n"),
        ),
    )

    structural = [node for node in graph.nodes if node.kind in {"File", "Symbol", "CodeRegion"}]
    versions = [node for node in graph.nodes if node.kind in {"FileVersion", "SymbolVersion", "CodeRegionVersion"}]
    version_edges = [edge for edge in graph.edges if edge.kind == "VERSION_OF"]

    assert len(versions) == len(structural)
    assert len(version_edges) == len(structural)
    assert version_id(file_id(repo_id, "src/auth/session.py"), "baseline:working-tree") in {node.id for node in versions}
    assert all(edge.source_id.startswith("version:") for edge in version_edges)
    assert all(edge.metadata["snapshot_id"] == "baseline:working-tree" for edge in version_edges)


def test_python_bootstrap_adds_local_import_edges() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/pkg/a.py", text="from .b import helper\n\ndef run():\n    return helper()\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    file_a = file_id(repo_id, "src/pkg/a.py")
    file_b = file_id(repo_id, "src/pkg/b.py")
    import_edges = [edge for edge in graph.edges if edge.kind == "IMPORTS"]

    assert len(import_edges) == 1
    assert import_edges[0].source_id == file_a
    assert import_edges[0].target_id == file_b
    assert import_edges[0].metadata["module"] == "pkg.b"
    assert import_edges[0].metadata["imported_name"] == "helper"


def test_python_bootstrap_resolves_init_relative_imports_to_sibling_module() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/pkg/__init__.py", text="from .b import helper\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    init_file = file_id(repo_id, "src/pkg/__init__.py")
    sibling_file = file_id(repo_id, "src/pkg/b.py")
    import_edges = [edge for edge in graph.edges if edge.kind == "IMPORTS"]

    assert len(import_edges) == 1
    assert import_edges[0].source_id == init_file
    assert import_edges[0].target_id == sibling_file
    assert import_edges[0].metadata["module"] == "pkg.b"


def test_python_bootstrap_adds_conservative_same_file_call_edges() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/pkg/a.py",
                text=(
                    "class A:\n"
                    "    def run(self):\n"
                    "        self.local()\n"
                    "        helper()\n"
                    "    def local(self):\n"
                    "        return True\n"
                    "\n"
                    "def helper():\n"
                    "    return True\n"
                ),
            ),
        ),
    )

    run_id = symbol_id(repo_id, "src/pkg/a.py", "A.run", "method")
    local_id = symbol_id(repo_id, "src/pkg/a.py", "A.local", "method")
    helper_id = symbol_id(repo_id, "src/pkg/a.py", "helper", "function")
    call_edges = {(edge.source_id, edge.target_id, edge.metadata["resolution"]) for edge in graph.edges if edge.kind == "CALLS"}

    assert (run_id, local_id, "self_method_same_class") in call_edges
    assert (run_id, helper_id, "same_file_unique_name") in call_edges


def test_python_bootstrap_adds_conservative_cross_file_call_edge_for_from_import() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/pkg/a.py", text="from .b import helper\n\ndef run():\n    return helper()\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    run_id = symbol_id(repo_id, "src/pkg/a.py", "run", "function")
    helper_id = symbol_id(repo_id, "src/pkg/b.py", "helper", "function")
    call_edges = {(edge.source_id, edge.target_id, edge.metadata["resolution"], edge.metadata["target_path"]) for edge in graph.edges if edge.kind == "CALLS"}

    assert (run_id, helper_id, "imported_symbol_name", "src/pkg/b.py") in call_edges


def test_python_bootstrap_adds_conservative_cross_file_call_edge_for_module_alias() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/pkg/a.py", text="import pkg.b as b\n\ndef run():\n    return b.helper()\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    run_id = symbol_id(repo_id, "src/pkg/a.py", "run", "function")
    helper_id = symbol_id(repo_id, "src/pkg/b.py", "helper", "function")
    call_edges = {(edge.source_id, edge.target_id, edge.metadata["resolution"], edge.metadata["imported_name"]) for edge in graph.edges if edge.kind == "CALLS"}

    assert (run_id, helper_id, "imported_module_attribute", "b.helper") in call_edges


def test_python_cross_file_call_resolver_does_not_invent_method_attribute_targets() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/pkg/a.py", text="import pkg.b as b\n\ndef run():\n    return b.helper()\n"),
            SourceFile(path="src/pkg/b.py", text="class Service:\n    def helper(self):\n        return True\n"),
        ),
    )

    run_id = symbol_id(repo_id, "src/pkg/a.py", "run", "function")
    service_helper_id = symbol_id(repo_id, "src/pkg/b.py", "Service.helper", "method")
    call_edges = {(edge.source_id, edge.target_id, edge.kind) for edge in graph.edges}

    assert (run_id, service_helper_id, "CALLS") not in call_edges


def test_python_cross_file_call_resolver_skips_dotted_import_without_alias() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(path="src/pkg/a.py", text="import pkg.b\n\ndef run():\n    return b.helper()\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    run_id = symbol_id(repo_id, "src/pkg/a.py", "run", "function")
    helper_id = symbol_id(repo_id, "src/pkg/b.py", "helper", "function")
    call_edges = {(edge.source_id, edge.target_id, edge.kind) for edge in graph.edges}

    assert (run_id, helper_id, "CALLS") not in call_edges


def test_web_bootstrap_adds_js_ts_symbols_without_raw_ast_nodes() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="web/app.tsx",
                text=(
                    "export function loadUser() { return true }\n"
                    "export const SignupButton = () => <button />\n"
                    "class SessionStore {}\n"
                ),
            ),
        ),
    )

    labels = {node.label for node in graph.nodes if node.kind == "Symbol"}
    symbol_kinds = {node.label: node.metadata["symbol_kind"] for node in graph.nodes if node.kind == "Symbol"}
    version_ids = {node.id for node in graph.nodes if node.kind == "SymbolVersion"}

    assert {"loadUser", "SignupButton", "SessionStore"}.issubset(labels)
    assert symbol_kinds["loadUser"] == "function"
    assert symbol_kinds["SignupButton"] == "component"
    assert symbol_kinds["SessionStore"] == "class"
    assert "CodeNode" not in {node.kind for node in graph.nodes}
    assert symbol_id(repo_id, "web/app.tsx", "SignupButton", "component") in {
        edge.target_id for edge in graph.edges if edge.kind == "DEFINES"
    }
    assert version_ids


def test_web_bootstrap_adds_css_and_html_code_regions() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(path="web/app.css", text=".signup-button, .primary {\n  color: green;\n}\n"),
            SourceFile(path="web/index.html", text='<button id="signup" class="primary cta">Join</button>\n'),
        ),
    )

    regions = {(node.label, node.metadata["region_kind"]) for node in graph.nodes if node.kind == "CodeRegion"}

    assert (".signup-button, .primary", "css_selector") in regions
    assert ("button#signup.primary.cta", "html_element") in regions
    assert "Symbol" not in {
        node.kind
        for node in graph.nodes
        if node.metadata.get("path") in {"web/app.css", "web/index.html"}
    }


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


def test_query_returns_doc_support_card_for_symbol_docstring() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth/session.py",
                text='''class AuthSession:\n    def refresh(self):\n        """Refresh tokens before redirect checks."""\n        return True\n''',
            ),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="fix redirect after token refresh",
            symbols=("AuthSession.refresh",),
            max_cards=3,
            session_id="s1",
        ),
    )

    assert [card.type for card in response.cards[:2]] == ["symbol_context", "doc_support"]
    doc_card = response.cards[1]
    assert doc_card.title == "Use docs for AuthSession.refresh"
    assert doc_card.evidence[0]["kind"] == "DocString"
    assert doc_card.evidence[2]["kind"] == "DOCUMENTS_SYMBOL"
    assert doc_card.confidence == 0.86
    assert doc_card.evidence[0]["node_id"] in response.trace["nodes"]
    assert response.next_actions[1].target == "src/auth/session.py"


def test_file_context_fills_budget_with_file_child_symbols_after_explicit_anchors() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/auth/session.py",
                text=(
                    "class AuthSession:\n"
                    "    def refresh(self):\n"
                    "        return True\n"
                    "\n"
                    "def helper():\n"
                    "    return AuthSession()\n"
                ),
            ),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect auth session",
            files=("src/auth/session.py",),
            max_cards=3,
        ),
    )

    assert response.status == "partial_structural"
    assert [card.type for card in response.cards] == ["next_file", "symbol_context", "symbol_context"]
    assert response.cards[1].title == "Inspect AuthSession"


def test_file_context_prioritizes_import_dependency_cards_before_child_symbol_fill() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(path="src/pkg/a.py", text="from .b import helper\n\ndef run():\n    return helper()\n"),
            SourceFile(path="src/pkg/b.py", text="def helper():\n    return True\n"),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect imported helper behavior",
            files=("src/pkg/a.py",),
            max_cards=3,
        ),
    )

    assert [card.type for card in response.cards] == ["next_file", "dependency", "symbol_context"]
    assert response.cards[1].title == "Check imported file src/pkg/b.py"
    assert response.cards[1].evidence[2]["kind"] == "IMPORTS"
    assert response.cards[1].evidence[2]["source_id"] == file_id("repo:test", "src/pkg/a.py")
    assert response.cards[1].evidence[2]["target_id"] == file_id("repo:test", "src/pkg/b.py")
    assert {"source_id": file_id("repo:test", "src/pkg/a.py"), "target_id": file_id("repo:test", "src/pkg/b.py"), "kind": "IMPORTS"} in response.trace["edges"]
    assert response.trace["versions"]


def test_symbol_context_includes_call_dependency_cards() -> None:
    graph = build_structural_graph(
        "repo:test",
        (
            SourceFile(
                path="src/pkg/a.py",
                text="def run():\n    return helper()\n\ndef helper():\n    return True\n",
            ),
        ),
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect run behavior",
            symbols=("run",),
            max_cards=2,
        ),
    )

    assert [card.type for card in response.cards] == ["symbol_context", "dependency"]
    assert response.cards[1].title == "Check called symbol helper"
    assert response.cards[1].evidence[2]["kind"] == "CALLS"
    assert {
        "source_id": symbol_id("repo:test", "src/pkg/a.py", "run", "function"),
        "target_id": symbol_id("repo:test", "src/pkg/a.py", "helper", "function"),
        "kind": "CALLS",
    } in response.trace["edges"]
    assert response.trace["versions"]


def test_historical_relation_card_requires_minimum_occurrences() -> None:
    graph = _cochanged_auth_graph(("aaa111", "bbb222"))

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="edit login safely",
            symbols=("login",),
            max_cards=4,
            session_id="s1",
        ),
    )

    assert "historical_relation" not in {card.type for card in response.cards}


def test_historical_relation_card_surfaces_after_conservative_gate() -> None:
    graph = _cochanged_auth_graph(("aaa111", "bbb222", "ccc333"))

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="edit login safely",
            symbols=("login",),
            max_cards=4,
            session_id="s1",
        ),
    )

    historical = next(card for card in response.cards if card.type == "historical_relation")
    edge_evidence = next(evidence for evidence in historical.evidence if evidence.get("kind") == "CO_CHANGED_WITH")

    assert historical.title == "Inspect historically co-changed refresh"
    assert "co-changed 3 times" in historical.why
    assert edge_evidence["stored_strength"] == "0.45"
    assert edge_evidence["cochange_count"] == "3"
    assert edge_evidence["min_cochange_count"] == "3"
    assert len(response.trace["occurrences"]) == 3
    assert response.next_actions[1].target == symbol_id("repo:test", "src/auth.py", "refresh", "function")


def test_historical_relation_threshold_is_configurable_for_eval() -> None:
    graph = _cochanged_auth_graph(("aaa111", "bbb222"))

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="edit login safely",
            symbols=("login",),
            max_cards=4,
            session_id="s1",
        ),
        historical_relation_policy=HistoricalRelationPolicy(min_cochange_count=2),
    )

    historical = next(card for card in response.cards if card.type == "historical_relation")
    edge_evidence = next(evidence for evidence in historical.evidence if evidence.get("kind") == "CO_CHANGED_WITH")
    assert edge_evidence["cochange_count"] == "2"
    assert edge_evidence["min_cochange_count"] == "2"


def test_historical_relation_cites_task_relevant_occurrences_first() -> None:
    graph = _cochanged_auth_graph(
        ("aaa111", "bbb222", "ccc333"),
        messages={
            "aaa111": "cleanup auth pair",
            "bbb222": "fix login redirect",
            "ccc333": "refresh token maintenance",
        },
    )

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="fix login redirect",
            symbols=("login",),
            max_cards=4,
            session_id="s1",
        ),
    )

    historical = next(card for card in response.cards if card.type == "historical_relation")
    occurrences = [evidence for evidence in historical.evidence if evidence.get("kind") == "RelationOccurrence"]

    assert occurrences[0]["commit_id"] == "commit:repo:test:bbb222"
    assert occurrences[0]["task_relevance"] == "task_match"
    assert occurrences[0]["matched_terms"] == "fix,login,redirect"
    assert any(evidence["task_relevance"] == "structural_fallback" for evidence in occurrences[1:])


def test_historical_relation_can_require_task_relevant_occurrence() -> None:
    graph = _cochanged_auth_graph(("aaa111", "bbb222", "ccc333"))

    response = answer_structural_query(
        graph,
        HarnessQueryRequest(
            intent="file_context",
            user_goal="signin redirect",
            symbols=("login",),
            max_cards=4,
            session_id="s1",
        ),
        historical_relation_policy=HistoricalRelationPolicy(require_task_relevant_occurrence=True),
    )

    assert "historical_relation" not in {card.type for card in response.cards}


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


def _cochanged_auth_graph(shas: tuple[str, ...], *, messages: dict[str, str] | None = None):
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
    for sha in shas:
        delta = build_commit_update_delta(
            graph,
            CommitWorkWindow(
                repo_id=repo_id,
                session_id=f"session-{sha}",
                commit_sha=sha,
                commit_message=(messages or {}).get(sha, "update auth pair"),
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
        apply_graph_update_delta(store, delta)
    return store.to_graph()
