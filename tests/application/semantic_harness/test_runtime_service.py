from __future__ import annotations

import pytest

from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessRuntimeService
from agent_memory_orchestrator.domain.semantic_harness import CommitHunk
from agent_memory_orchestrator.domain.semantic_harness import CommitWorkWindow
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import HunkRange
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import build_commit_update_delta
from agent_memory_orchestrator.domain.semantic_harness import file_id


def test_runtime_bootstrap_persists_graph_and_answers_query(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text(
        'def refresh_token():\n    """Refresh token before redirect."""\n    return True\n',
        encoding="utf-8",
    )

    runtime = SemanticHarnessRuntimeService()
    bootstrap = runtime.bootstrap_repo(tmp_path, repo_id="repo:test")
    response = runtime.query(
        "repo:test",
        HarnessQueryRequest(
            intent="file_context",
            user_goal="inspect refresh token",
            symbols=("refresh_token",),
            max_cards=2,
        ),
    )

    assert bootstrap.repo_id == "repo:test"
    assert bootstrap.file_count == 1
    assert bootstrap.graph_snapshot.node_count > 0
    assert bootstrap.projection_document_count > 0
    assert response.status == "partial_structural"
    assert response.cards


def test_runtime_query_returns_unavailable_for_missing_repo() -> None:
    runtime = SemanticHarnessRuntimeService()

    response = runtime.query(
        "repo:missing",
        HarnessQueryRequest(intent="edit_plan", user_goal="fix login"),
    )

    assert response.status == "unavailable"
    assert response.cards == ()
    assert response.warnings == ("repo_not_bootstrapped:repo:missing",)


def test_runtime_caches_loaded_graph_for_repeated_queries() -> None:
    graph = StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(
                id=file_id("repo:test", "src/auth.py"),
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                status="active",
                metadata={"path": "src/auth.py"},
            ),
        ),
        edges=(),
    )
    store = _CountingGraphStore(graph)
    runtime = SemanticHarnessRuntimeService(graph_repository=_SingleGraphRepository(store))
    request = HarnessQueryRequest(intent="file_context", user_goal="inspect auth", files=("src/auth.py",))

    first = runtime.query("repo:test", request)
    second = runtime.query("repo:test", request)

    assert first.status == "partial_structural"
    assert second.status == "partial_structural"
    assert store.to_graph_calls == 1


def test_explicit_mode_uses_evidence_slice_without_loading_full_graph() -> None:
    graph = StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(
                id=file_id("repo:test", "src/auth.py"),
                kind="File",
                label="src/auth.py",
                repo_id="repo:test",
                metadata={
                    "path": "src/auth.py",
                    "semantic_facts": [
                        {
                            "fact_id": "fact:role",
                            "fact_type": "semantic_role",
                            "text": "Owns authentication policy.",
                            "review_status": "accepted",
                            "confidence": 0.9,
                        }
                    ],
                },
            ),
        ),
        edges=(),
    )
    repository = _SliceOnlyRepository(graph)
    runtime = SemanticHarnessRuntimeService(graph_repository=repository)

    response = runtime.query(
        "repo:test",
        HarnessQueryRequest(
            intent="file_context",
            mode="context_for_anchor",
            user_goal="understand auth",
            files=("src/auth.py",),
            questions=("what is this file responsible for?",),
        ),
    )

    assert repository.query_calls == 1
    assert repository.load_calls == 0
    assert response.status == "ready"
    assert response.mode_result["answers"][0]["fact_id"] == "fact:role"


def test_runtime_apply_delta_updates_persisted_graph_and_projection(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "auth.py").write_text("def login():\n    return False\n", encoding="utf-8")
    runtime = SemanticHarnessRuntimeService()
    bootstrap = runtime.bootstrap_repo(tmp_path, repo_id="repo:test")
    graph = runtime.load_graph("repo:test")
    if graph is None:
        pytest.fail("runtime did not persist bootstrap graph")
    delta = build_commit_update_delta(
        graph,
        CommitWorkWindow(
            repo_id="repo:test",
            session_id="session-1",
            commit_sha="abc123456789",
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

    applied = runtime.apply_delta(delta)
    updated_graph = runtime.load_graph("repo:test")

    assert applied.apply_result.status == "applied"
    assert applied.graph_snapshot.graph_snapshot_id != bootstrap.graph_snapshot.graph_snapshot_id
    assert applied.projection.graph_snapshot_id == applied.graph_snapshot.graph_snapshot_id
    assert updated_graph is not None
    assert delta.commit_id in {node.id for node in updated_graph.nodes}


class _CountingGraphStore:
    def __init__(self, graph: StructuralHarnessGraph) -> None:
        self._graph = graph
        self.to_graph_calls = 0

    @property
    def repo_id(self) -> str:
        return self._graph.repo_id

    def to_graph(self) -> StructuralHarnessGraph:
        self.to_graph_calls += 1
        return self._graph


class _SingleGraphRepository:
    def __init__(self, store: _CountingGraphStore) -> None:
        self._store = store

    def load(self, repo_id: str) -> _CountingGraphStore | None:
        if repo_id == self._store.repo_id:
            return self._store
        return None

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> _CountingGraphStore:
        self._store = _CountingGraphStore(graph)
        return self._store


class _SliceOnlyRepository:
    def __init__(self, graph: StructuralHarnessGraph) -> None:
        self.graph = graph
        self.query_calls = 0
        self.load_calls = 0

    def query_evidence(self, _plan) -> StructuralHarnessGraph:
        self.query_calls += 1
        return self.graph

    def load(self, _repo_id: str):
        self.load_calls += 1
        raise AssertionError("explicit query must not load the full graph")

    def replace_from_graph(self, graph: StructuralHarnessGraph):
        self.graph = graph
        return _CountingGraphStore(graph)
