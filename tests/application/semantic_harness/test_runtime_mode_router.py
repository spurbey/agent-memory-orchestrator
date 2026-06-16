from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessRuntimeService
from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import file_id


def test_runtime_routes_explicit_context_for_anchor_mode_to_typed_result() -> None:
    runtime = SemanticHarnessRuntimeService(graph_repository=_SingleGraphRepository(_Store(_graph())))

    response = runtime.query(
        "repo:test",
        HarnessQueryRequest(
            intent="file_context",
            mode="context_for_anchor",
            user_goal="avoid changing snapshot identity incorrectly",
            files=("src/snapshots.py",),
            questions=("what invariant does this file maintain?",),
        ),
    )

    assert response.status == "ready"
    assert response.intent_used == "context_for_anchor"
    assert response.cards == ()
    assert response.mode_result["answers"][0]["question_type"] == "invariant"
    assert "Duplicate raw observations" in response.mode_result["answers"][0]["answer"]


def test_runtime_keeps_legacy_file_context_on_card_path_without_explicit_mode() -> None:
    runtime = SemanticHarnessRuntimeService(graph_repository=_SingleGraphRepository(_Store(_graph())))

    response = runtime.query(
        "repo:test",
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect snapshots",
            files=("src/snapshots.py",),
            max_cards=2,
        ),
    )

    assert response.intent_used == "file_context"
    assert response.cards
    assert response.mode_result == {}


def _graph() -> StructuralHarnessGraph:
    file_node = HarnessNode(
        id=file_id("repo:test", "src/snapshots.py"),
        kind="File",
        label="src/snapshots.py",
        repo_id="repo:test",
        summary="Owns structural graph snapshot identity.",
        metadata={
            "path": "src/snapshots.py",
            "invariant": "Duplicate raw observations must not change structural identity.",
        },
    )
    test_node = HarnessNode(
        id=file_id("repo:test", "tests/test_snapshots.py"),
        kind="File",
        label="tests/test_snapshots.py",
        repo_id="repo:test",
        metadata={"path": "tests/test_snapshots.py"},
    )
    return StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(file_node, test_node),
        edges=(HarnessEdge(source_id=file_node.id, target_id=test_node.id, kind="VALIDATED_BY"),),
    )


class _Store:
    def __init__(self, graph: StructuralHarnessGraph) -> None:
        self._graph = graph

    @property
    def repo_id(self) -> str:
        return self._graph.repo_id

    def to_graph(self) -> StructuralHarnessGraph:
        return self._graph


class _SingleGraphRepository:
    def __init__(self, store: _Store) -> None:
        self._store = store

    def load(self, repo_id: str) -> _Store | None:
        if repo_id == self._store.repo_id:
            return self._store
        return None

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> _Store:
        self._store = _Store(graph)
        return self._store
