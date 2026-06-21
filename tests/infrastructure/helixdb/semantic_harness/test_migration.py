from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import InMemoryHarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.infrastructure.helixdb.semantic_harness import migration


class _SourceRepository:
    graph: StructuralHarnessGraph

    def __init__(self, _path) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def load(self, repo_id: str):
        assert repo_id == self.graph.repo_id
        return InMemoryHarnessGraphStore.from_graph(self.graph)


class _TargetRepository:
    graph: StructuralHarnessGraph | None = None

    def __init__(self, _config) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def healthy(self) -> bool:
        return True

    def replace_from_graph(self, graph: StructuralHarnessGraph):
        self.graph = graph
        return InMemoryHarnessGraphStore.from_graph(graph)

    def load(self, repo_id: str):
        assert self.graph is not None
        assert repo_id == self.graph.repo_id
        return InMemoryHarnessGraphStore.from_graph(self.graph)


def test_migration_verifies_structural_snapshot(monkeypatch, tmp_path) -> None:
    graph = StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(
            HarnessNode(id="repo:test", kind="Repo", label="repo:test", repo_id="repo:test"),
            HarnessNode(id="file:repo:test:a.py", kind="File", label="a.py", repo_id="repo:test"),
        ),
        edges=(HarnessEdge(source_id="repo:test", target_id="file:repo:test:a.py", kind="CONTAINS"),),
    )
    _SourceRepository.graph = graph
    _TargetRepository.graph = None
    monkeypatch.setattr(migration, "SQLiteHarnessGraphRepository", _SourceRepository)
    monkeypatch.setattr(migration, "HelixHarnessGraphRepository", _TargetRepository)

    result = migration.migrate_sqlite_repo_to_helix(
        repo_id="repo:test",
        sqlite_path=tmp_path / "legacy.sqlite",
    )

    assert result["verified"] is True
    assert result["node_count"] == 2
    assert result["edge_count"] == 1
    assert result["graph_snapshot_id"].startswith("gsnap:repo:test:")
