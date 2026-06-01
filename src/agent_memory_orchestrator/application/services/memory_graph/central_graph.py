from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....domain.retrieval.policy import _sanitize_output_node
from ....infrastructure.kuzu import GraphStore
from .repo_scope import matches_repo_scope
from .version_flow import _is_central_graph_seed
from .version_flow import _is_isolated_graph_seed
from .version_flow import _isolated_graph_seed_pool


def central_graph(
    *,
    graph_store: GraphStore,
    merge_status: Callable[[], dict[str, Any]],
    limit: int = 100,
    full: bool = False,
    repo_id: str = "",
) -> dict[str, Any]:
    max_limit = 10000 if full else 500
    safe_limit = max(1, min(max_limit, int(limit)))
    safe_repo_id = str(repo_id or "").strip()
    all_nodes = [
        node
        for node in graph_store.list_nodes(limit=safe_limit * 8)
        if matches_repo_scope(node, safe_repo_id)
    ]
    pool = [
        *[
            node
            for node in graph_store.list_nodes(status="committed", limit=safe_limit)
            if matches_repo_scope(node, safe_repo_id)
        ],
        *[
            node
            for node in graph_store.list_nodes(status="active", limit=safe_limit)
            if matches_repo_scope(node, safe_repo_id)
        ],
        *all_nodes,
    ]
    output_ids: set[str] = set()
    nodes: list[dict[str, Any]] = []
    isolated_pool: list[dict[str, Any]] = []
    for node in pool:
        node_id = str(node.get("id") or "")
        if node_id in output_ids:
            continue
        if _is_central_graph_seed(node):
            nodes.append(_sanitize_output_node(node))
            output_ids.add(node_id)
        if len(nodes) >= safe_limit:
            break
    if not nodes:
        isolated_pool = _isolated_graph_seed_pool(graph_store, all_nodes, limit=safe_limit)
        for node in isolated_pool:
            node_id = str(node.get("id") or "")
            if node_id in output_ids:
                continue
            if not _is_isolated_graph_seed(node):
                continue
            nodes.append(_sanitize_output_node(node))
            output_ids.add(node_id)
            if len(nodes) >= safe_limit:
                break
    node_by_id = {str(node.get("id") or ""): node for node in all_nodes + pool + isolated_pool}
    all_edges = graph_store.list_edges(limit=safe_limit * 32)
    central_edges: list[dict[str, Any]] = []
    edge_ids: set[str] = set()
    frontier = set(output_ids)
    for _depth in range(3):
        if not frontier:
            break
        next_frontier: set[str] = set()
        for edge in all_edges:
            edge_id = str(edge.get("id") or "")
            source_id = str(edge.get("source_id") or "")
            target_id = str(edge.get("target_id") or "")
            if not source_id or not target_id:
                continue
            if source_id not in frontier and target_id not in frontier:
                continue
            missing_endpoint_ids = [
                endpoint_id for endpoint_id in (source_id, target_id) if endpoint_id not in output_ids
            ]
            if len(nodes) + len(missing_endpoint_ids) > safe_limit:
                continue
            missing_endpoints: list[tuple[str, dict[str, Any]]] = []
            for endpoint_id in missing_endpoint_ids:
                endpoint = node_by_id.get(endpoint_id)
                if not endpoint:
                    missing_endpoints = []
                    break
                missing_endpoints.append((endpoint_id, endpoint))
            if len(missing_endpoints) != len(missing_endpoint_ids):
                continue
            if edge_id not in edge_ids:
                central_edges.append(edge)
                edge_ids.add(edge_id)
            for endpoint_id, endpoint in missing_endpoints:
                nodes.append(_sanitize_output_node(endpoint))
                output_ids.add(endpoint_id)
                next_frontier.add(endpoint_id)
            if len(central_edges) >= safe_limit * 4:
                break
        frontier = next_frontier
        if len(central_edges) >= safe_limit * 4:
            break
    return {
        "ok": True,
        "repo_id": safe_repo_id,
        "nodes": nodes,
        "edges": central_edges[: safe_limit * 4],
        "full": full,
        "limit": safe_limit,
        "status": merge_status(),
        "warnings": central_graph_warnings(nodes, central_edges),
    }


def central_graph_warnings(nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    node_ids = {str(node.get("id") or "") for node in nodes}
    dangling = [
        edge
        for edge in edges
        if str(edge.get("source_id") or "") not in node_ids or str(edge.get("target_id") or "") not in node_ids
    ]
    if nodes and len(edges) < max(1, len(nodes) // 5):
        warnings.append("central_graph_edges_sparse")
    if dangling:
        warnings.append("central_graph_has_dangling_visible_edges")
    version_edges = [
        edge
        for edge in edges
        if edge.get("kind")
        in {
            "COMMITTED_AS",
            "REFINES",
            "SUPERSEDES",
            "DUPLICATE_OF",
            "CONTRADICTS",
            "REASON_NODE_EXPLAINS_COMMIT",
            "REASON_NODE_IN_PACKET",
            "COMMIT_PRODUCED_HUNK",
        }
    ]
    if nodes and not version_edges:
        warnings.append("central_graph_has_no_visible_version_edges")
    return warnings


__all__ = ["central_graph", "central_graph_warnings"]
