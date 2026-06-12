from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import graph_snapshot_id
from agent_memory_orchestrator.domain.semantic_harness import graph_snapshot_identity


def test_graph_snapshot_id_ignores_mutable_node_and_edge_metadata() -> None:
    first = _graph(
        node_summary="old summary",
        node_metadata={"change_stats": 1},
        edge_weight=0.4,
        edge_metadata={"cochange_count": 1},
    )
    second = _graph(
        node_summary="new summary",
        node_metadata={"change_stats": 99},
        edge_weight=0.9,
        edge_metadata={"cochange_count": 10},
    )

    assert graph_snapshot_id(first) == graph_snapshot_id(second)


def test_graph_snapshot_id_changes_when_structural_edge_key_changes() -> None:
    first = _graph(edge_kind="DEFINES")
    second = _graph(edge_kind="CONTAINS")

    assert graph_snapshot_id(first) != graph_snapshot_id(second)


def test_graph_snapshot_identity_reports_counts_and_schema() -> None:
    graph = _graph()
    identity = graph_snapshot_identity(graph)

    assert identity.repo_id == "repo:test"
    assert identity.graph_schema_version == "semantic_harness_graph_v1"
    assert identity.graph_snapshot_id == graph_snapshot_id(graph)
    assert identity.node_count == 2
    assert identity.edge_count == 1
    assert identity.as_dict()["graph_snapshot_id"] == identity.graph_snapshot_id


def _graph(
    *,
    node_summary: str = "",
    node_metadata: dict[str, object] | None = None,
    edge_kind: str = "DEFINES",
    edge_weight: float = 1.0,
    edge_metadata: dict[str, object] | None = None,
) -> StructuralHarnessGraph:
    file_node = HarnessNode(id="file:repo:test:src/a.py", kind="File", label="src/a.py", repo_id="repo:test")
    symbol_node = HarnessNode(
        id="symbol:repo:test:src/a.py:run:function",
        kind="Symbol",
        label="run",
        repo_id="repo:test",
        summary=node_summary,
        metadata=dict(node_metadata or {}),
    )
    edge = HarnessEdge(
        source_id=file_node.id,
        target_id=symbol_node.id,
        kind=edge_kind,
        weight=edge_weight,
        metadata=dict(edge_metadata or {}),
    )
    return StructuralHarnessGraph(repo_id="repo:test", nodes=(file_node, symbol_node), edges=(edge,))
