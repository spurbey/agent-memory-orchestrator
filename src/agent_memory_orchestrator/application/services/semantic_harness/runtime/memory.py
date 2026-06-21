from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field

from agent_memory_orchestrator.domain.semantic_harness import HarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import GraphSlicePlan
from agent_memory_orchestrator.domain.semantic_harness import InMemoryHarnessGraphStore
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import resolve_anchors


@dataclass(slots=True)
class InMemoryHarnessGraphRepository:
    """Process-local graph repository for runtime service tests and smoke use."""

    _stores: dict[str, InMemoryHarnessGraphStore] = field(default_factory=dict)

    def load(self, repo_id: str) -> HarnessGraphStore | None:
        return self._stores.get(repo_id)

    def replace_from_graph(self, graph: StructuralHarnessGraph) -> HarnessGraphStore:
        store = InMemoryHarnessGraphStore.from_graph(graph)
        self._stores[graph.repo_id] = store
        return store

    def query_evidence(self, plan: GraphSlicePlan) -> StructuralHarnessGraph | None:
        store = self._stores.get(plan.repo_id)
        if store is None:
            return None
        graph = store.to_graph()
        resolved_ids: list[str] = []
        for seed in plan.seeds:
            if seed.kind == "file":
                anchors = resolve_anchors(graph, files=(seed.value,))
            else:
                symbol = f"{seed.path_hint}::{seed.value}" if seed.path_hint else seed.value
                anchors = resolve_anchors(graph, symbols=(symbol,))
            resolved_ids.extend(anchor.node_id for anchor in anchors.resolved)

        node_by_id = graph.node_by_id()
        nodes = {node_id: node_by_id[node_id] for node_id in resolved_ids if node_id in node_by_id}
        edges = {}
        seed_ids = tuple(nodes)
        for expansion in plan.expansions:
            frontier = seed_ids
            visited = set(seed_ids)
            for _depth in range(max(1, min(3, expansion.depth))):
                next_frontier: list[str] = []
                for node_id in frontier:
                    traversed = (
                        graph.incoming(node_id, kind=expansion.kind)
                        if expansion.direction == "incoming"
                        else graph.outgoing(node_id, kind=expansion.kind)
                    )[: expansion.max_neighbors]
                    for edge in traversed:
                        neighbor_id = edge.source_id if expansion.direction == "incoming" else edge.target_id
                        if neighbor := node_by_id.get(neighbor_id):
                            nodes.setdefault(neighbor.id, neighbor)
                            edges[(edge.source_id, edge.target_id, edge.kind)] = edge
                            if neighbor.id not in visited:
                                visited.add(neighbor.id)
                                next_frontier.append(neighbor.id)
                        if len(nodes) >= plan.max_nodes or len(edges) >= plan.max_edges:
                            break
                frontier = tuple(next_frontier)
                if not frontier or len(nodes) >= plan.max_nodes or len(edges) >= plan.max_edges:
                    break
        return StructuralHarnessGraph(
            repo_id=plan.repo_id,
            nodes=tuple(nodes.values())[: plan.max_nodes],
            edges=tuple(edges.values())[: plan.max_edges],
        )


__all__ = ["InMemoryHarnessGraphRepository"]
