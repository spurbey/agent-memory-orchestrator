from __future__ import annotations

from helixdb import NodeRef
from helixdb import Predicate
from helixdb import g
from helixdb import read_batch

from agent_memory_orchestrator.domain.semantic_harness import EdgeExpansion
from agent_memory_orchestrator.domain.semantic_harness import GraphSeed
from agent_memory_orchestrator.domain.semantic_harness import GraphSlicePlan
from agent_memory_orchestrator.domain.semantic_harness import HarnessEdge
from agent_memory_orchestrator.domain.semantic_harness import HarnessNode
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.identity import file_id
from agent_memory_orchestrator.domain.semantic_harness.identity import normalize_file_path

from .client import HelixHarnessClient
from .codec import edge_from_properties
from .codec import node_from_properties
from .codec import result_properties


class HelixEvidenceQuery:
    """Executes backend-neutral graph-slice plans with Helix traversals."""

    def __init__(self, client: HelixHarnessClient) -> None:
        self._client = client

    def execute(self, plan: GraphSlicePlan) -> StructuralHarnessGraph:
        nodes = {node.id: node for node in self._resolve_seeds(plan.repo_id, plan.seeds)}
        edges: dict[tuple[str, str, str], HarnessEdge] = {}
        seed_nodes = tuple(nodes.values())
        for expansion in plan.expansions:
            self._expand(
                repo_id=plan.repo_id,
                seeds=seed_nodes,
                expansion=expansion,
                nodes=nodes,
                edges=edges,
                max_nodes=max(1, plan.max_nodes),
                max_edges=max(0, plan.max_edges),
            )
        valid_edges = tuple(
            edge
            for edge in edges.values()
            if edge.source_id in nodes and edge.target_id in nodes
        )
        return StructuralHarnessGraph(
            repo_id=plan.repo_id,
            nodes=tuple(nodes.values())[: plan.max_nodes],
            edges=valid_edges[: plan.max_edges],
        )

    def _resolve_seeds(self, repo_id: str, seeds: tuple[GraphSeed, ...]) -> tuple[HarnessNode, ...]:
        files = tuple(seed for seed in seeds if seed.kind == "file")
        symbols = tuple(seed for seed in seeds if seed.kind == "symbol")
        return (*self._file_seeds(repo_id, files), *self._symbol_seeds(repo_id, symbols))

    def _file_seeds(self, repo_id: str, seeds: tuple[GraphSeed, ...]) -> tuple[HarnessNode, ...]:
        if not seeds:
            return ()
        paths = tuple(normalize_file_path(seed.value).lower() for seed in seeds)
        exact = self._nodes(
            "File",
            Predicate.and_(
                (
                    Predicate.eq("repo_id", repo_id),
                    Predicate.or_(tuple(Predicate.eq("node_id", file_id(repo_id, path)) for path in paths)),
                )
            ),
            limit=len(paths),
        )
        exact_paths = {_node_path(node) for node in exact}
        unresolved = tuple(path for path in paths if path not in exact_paths)
        if not unresolved:
            return exact
        candidates = self._nodes(
            "File",
            Predicate.and_(
                (
                    Predicate.eq("repo_id", repo_id),
                    Predicate.or_(tuple(Predicate.contains("path", path) for path in unresolved)),
                )
            ),
            limit=min(256, len(unresolved) * 8),
        )
        suffix_matches = [
            node
            for path in unresolved
            if len(matches := [candidate for candidate in candidates if _node_path(candidate).endswith(path)]) == 1
            for node in matches
        ]
        return _dedupe_nodes((*exact, *suffix_matches))

    def _symbol_seeds(self, repo_id: str, seeds: tuple[GraphSeed, ...]) -> tuple[HarnessNode, ...]:
        if not seeds:
            return ()
        predicates = tuple(
            predicate
            for seed in seeds
            for predicate in (
                Predicate.eq("label", seed.value),
                Predicate.eq("qualified_name", seed.value),
                Predicate.contains("qualified_name", f".{seed.value}"),
            )
        )
        candidates = self._nodes(
            "Symbol",
            Predicate.and_((Predicate.eq("repo_id", repo_id), Predicate.or_(predicates))),
            limit=min(512, max(32, len(seeds) * 8)),
        )
        resolved: list[HarnessNode] = []
        for seed in seeds:
            symbol = seed.value.strip().lower()
            path = normalize_file_path(seed.path_hint).lower() if seed.path_hint else ""
            matches = [node for node in candidates if _symbol_matches(node, symbol=symbol, path_hint=path)]
            if len(matches) == 1:
                resolved.append(matches[0])
        return _dedupe_nodes(resolved)

    def _nodes(self, kind: str, predicate: Predicate, *, limit: int) -> tuple[HarnessNode, ...]:
        query = (
            read_batch()
            .var_as("nodes", g().n_with_label(kind).where(predicate).limit(limit).value_map())
            .returning(["nodes"])
        )
        rows = result_properties(self._client.send(query.to_dynamic_request()), "nodes")
        return tuple(node_from_properties(row) for row in rows)

    def _expand(
        self,
        *,
        repo_id: str,
        seeds: tuple[HarnessNode, ...],
        expansion: EdgeExpansion,
        nodes: dict[str, HarnessNode],
        edges: dict[tuple[str, str, str], HarnessEdge],
        max_nodes: int,
        max_edges: int,
    ) -> None:
        frontier = seeds
        visited = {node.id for node in seeds}
        for _depth in range(max(1, min(3, expansion.depth))):
            next_frontier: list[HarnessNode] = []
            sources_by_kind: dict[str, list[HarnessNode]] = {}
            for source in frontier:
                sources_by_kind.setdefault(source.kind, []).append(source)
            for sources in sources_by_kind.values():
                if len(nodes) >= max_nodes or len(edges) >= max_edges:
                    return
                neighbors, traversed_edges = self._one_hop(repo_id, tuple(sources), expansion)
                for neighbor in neighbors:
                    if neighbor.id not in nodes and len(nodes) < max_nodes:
                        nodes[neighbor.id] = neighbor
                    if neighbor.id not in visited:
                        visited.add(neighbor.id)
                        next_frontier.append(neighbor)
                for edge in traversed_edges:
                    if len(edges) >= max_edges:
                        break
                    edges[(edge.source_id, edge.target_id, edge.kind)] = edge
            frontier = tuple(next_frontier)
            if not frontier:
                return

    def _one_hop(
        self,
        repo_id: str,
        sources: tuple[HarnessNode, ...],
        expansion: EdgeExpansion,
    ) -> tuple[tuple[HarnessNode, ...], tuple[HarnessEdge, ...]]:
        if not sources:
            return (), ()
        start = g().n_with_label(sources[0].kind).where(
            Predicate.or_(tuple(Predicate.eq("node_id", source.id) for source in sources))
        )
        limit = min(4_096, expansion.max_neighbors * len(sources))
        if expansion.direction == "incoming":
            node_traversal = g().n(NodeRef.var("start")).in_(expansion.kind)
            edge_traversal = g().n(NodeRef.var("start")).in_e(expansion.kind)
        else:
            node_traversal = g().n(NodeRef.var("start")).out(expansion.kind)
            edge_traversal = g().n(NodeRef.var("start")).out_e(expansion.kind)
        query = (
            read_batch()
            .var_as("start", start)
            .var_as(
                "neighbors",
                node_traversal.where(Predicate.eq("repo_id", repo_id)).limit(limit).value_map(),
            )
            .var_as(
                "edges",
                edge_traversal.edge_has("repo_id", repo_id).limit(limit).edge_properties(),
            )
            .returning(["neighbors", "edges"])
        )
        result = self._client.send(query.to_dynamic_request())
        neighbors = tuple(node_from_properties(row) for row in result_properties(result, "neighbors"))
        edges = tuple(
            edge_from_properties(row, kind=expansion.kind)
            for row in result_properties(result, "edges")
        )
        return neighbors, edges


def _node_path(node: HarnessNode) -> str:
    return normalize_file_path(str(node.metadata.get("path") or node.label)).lower()


def _symbol_matches(node: HarnessNode, *, symbol: str, path_hint: str) -> bool:
    qualified = str(node.metadata.get("qualified_name") or node.label).lower()
    if qualified != symbol and not qualified.endswith(f".{symbol}"):
        return False
    path = _node_path(node)
    return not path_hint or path == path_hint or path.endswith(path_hint)


def _dedupe_nodes(nodes: tuple[HarnessNode, ...] | list[HarnessNode]) -> tuple[HarnessNode, ...]:
    by_id: dict[str, HarnessNode] = {}
    for node in nodes:
        by_id.setdefault(node.id, node)
    return tuple(by_id.values())


__all__ = ["HelixEvidenceQuery"]
