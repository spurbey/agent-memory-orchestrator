from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import CommitHunk
from agent_memory_orchestrator.domain.semantic_harness import HunkRange
from agent_memory_orchestrator.domain.semantic_harness import SourceFile
from agent_memory_orchestrator.domain.semantic_harness import build_structural_graph
from agent_memory_orchestrator.domain.semantic_harness import file_id
from agent_memory_orchestrator.domain.semantic_harness import map_hunk_to_entities
from agent_memory_orchestrator.domain.semantic_harness import symbol_id


def test_hunk_maps_to_single_python_symbol_with_high_confidence() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(
        repo_id,
        (
            SourceFile(
                path="src/auth.py",
                text=(
                    "def login():\n"
                    "    token = issue_token()\n"
                    "    return token\n"
                    "\n"
                    "def logout():\n"
                    "    return True\n"
                ),
            ),
        ),
    )

    mappings = map_hunk_to_entities(
        graph,
        CommitHunk(
            hunk_id="h1",
            file_path="src/auth.py",
            old_range=HunkRange(start=2, count=1),
            new_range=HunkRange(start=2, count=1),
        ),
    )

    assert len(mappings) == 1
    assert mappings[0].target_node_id == symbol_id(repo_id, "src/auth.py", "login", "function")
    assert mappings[0].target_kind == "Symbol"
    assert mappings[0].edge_kind == "MAPS_TO_SYMBOL"
    assert mappings[0].status == "mapped"
    assert mappings[0].confidence >= 0.9


def test_hunk_crossing_multiple_symbols_stays_review_only() -> None:
    graph = build_structural_graph(
        "repo:test",
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

    mappings = map_hunk_to_entities(
        graph,
        CommitHunk(
            hunk_id="h1",
            file_path="src/auth.py",
            old_range=HunkRange(start=1, count=4),
            new_range=HunkRange(start=1, count=4),
        ),
    )

    assert {mapping.status for mapping in mappings} == {"review_only"}
    assert {mapping.target_kind for mapping in mappings} == {"Symbol"}
    assert {mapping.target_node_id for mapping in mappings} == {
        symbol_id("repo:test", "src/auth.py", "login", "function"),
        symbol_id("repo:test", "src/auth.py", "logout", "function"),
    }


def test_hunk_maps_to_css_code_region() -> None:
    graph = build_structural_graph(
        "repo:test",
        (SourceFile(path="web/app.css", text=".signup-button {\n  color: green;\n}\n"),),
    )

    mappings = map_hunk_to_entities(
        graph,
        CommitHunk(
            hunk_id="h1",
            file_path="web/app.css",
            old_range=HunkRange(start=2, count=1),
            new_range=HunkRange(start=2, count=1),
        ),
    )

    assert len(mappings) == 1
    assert mappings[0].target_kind == "CodeRegion"
    assert mappings[0].edge_kind == "MAPS_TO_CODE_REGION"
    assert mappings[0].status == "mapped"


def test_hunk_falls_back_to_file_level_when_no_entity_span_matches() -> None:
    repo_id = "repo:test"
    graph = build_structural_graph(repo_id, (SourceFile(path="src/main.py", text="value = 1\n"),))

    mappings = map_hunk_to_entities(
        graph,
        CommitHunk(
            hunk_id="h1",
            file_path="src/main.py",
            old_range=HunkRange(start=1, count=1),
            new_range=HunkRange(start=1, count=1),
        ),
    )

    assert mappings[0].target_node_id == file_id(repo_id, "src/main.py")
    assert mappings[0].target_kind == "File"
    assert mappings[0].edge_kind == "CHANGED_IN"
    assert mappings[0].status == "file_level"
    assert mappings[0].confidence == 0.55


def test_hunk_for_unknown_file_is_unresolved() -> None:
    graph = build_structural_graph("repo:test", (SourceFile(path="src/main.py", text="value = 1\n"),))

    mappings = map_hunk_to_entities(
        graph,
        CommitHunk(
            hunk_id="h1",
            file_path="src/missing.py",
            old_range=HunkRange(start=1, count=1),
            new_range=HunkRange(start=1, count=1),
        ),
    )

    assert mappings[0].status == "unresolved"
    assert mappings[0].confidence == 0.0
