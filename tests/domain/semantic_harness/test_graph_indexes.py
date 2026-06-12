from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph


def test_structural_graph_uses_cached_indexes_without_reordering() -> None:
    file_node = HarnessNode(id="file:repo:test:src/a.py", kind="File", label="src/a.py", repo_id="repo:test")
    first_symbol = HarnessNode(id="symbol:repo:test:src/a.py:first:function", kind="Symbol", label="first", repo_id="repo:test")
    second_symbol = HarnessNode(id="symbol:repo:test:src/a.py:second:function", kind="Symbol", label="second", repo_id="repo:test")
    first_edge = HarnessEdge(source_id=file_node.id, target_id=first_symbol.id, kind="DEFINES")
    second_edge = HarnessEdge(source_id=file_node.id, target_id=second_symbol.id, kind="DEFINES")
    graph = StructuralHarnessGraph(
        repo_id="repo:test",
        nodes=(file_node, first_symbol, second_symbol),
        edges=(first_edge, second_edge),
    )

    assert graph.node_by_id() is graph.node_by_id()
    assert graph.nodes_by_kind("Symbol") == (first_symbol, second_symbol)
    assert graph.outgoing(file_node.id) == (first_edge, second_edge)
    assert graph.outgoing(file_node.id, kind="DEFINES") == (first_edge, second_edge)
    assert graph.incoming(second_symbol.id) == (second_edge,)
    assert graph.incoming(second_symbol.id, kind="DEFINES") == (second_edge,)
    assert graph.incoming(second_symbol.id, kind="CALLS") == ()
