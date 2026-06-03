from __future__ import annotations

import math
from typing import Any

from ....infrastructure.kuzu import KuzuGraphStore
from ....infrastructure.llm.text_embedder import StrictTextEmbedder
from .embeddings import CodeBertEmbedder
from .models import SessionGraphQueryOptions
from .models import SessionGraphQueryResult
from .models import SessionGraphSearchHit


def query_session_graph(options: SessionGraphQueryOptions) -> SessionGraphQueryResult:
    graph_path = options.graph_path.resolve()
    if not graph_path.exists():
        raise RuntimeError(f"graph_path_missing:{graph_path}")

    text_embedder: StrictTextEmbedder | None = None
    code_embedder: CodeBertEmbedder | None = None
    text_hits: list[SessionGraphSearchHit] = []
    code_hits: list[SessionGraphSearchHit] = []
    models: dict[str, Any] = {}

    store = KuzuGraphStore(graph_path)
    nodes = store.list_nodes(limit=10000)
    edges = store.list_edges(limit=10000)

    if options.query:
        text_embedder = StrictTextEmbedder(options.text_embedding_model)
        models["text_embedding_model"] = options.text_embedding_model
        models["text_embedding_dims"] = text_embedder.dims
        query_vector = text_embedder.embed(options.query)
        text_hits = _rank_nodes(
            nodes,
            edges,
            query_vector,
            embedding_key="text_embedding",
            limit=options.limit,
        )

    if options.code_query:
        code_embedder = CodeBertEmbedder(options.code_embedding_model)
        models["code_embedding_model"] = options.code_embedding_model
        models["code_embedding_dims"] = code_embedder.dims
        query_vector = code_embedder.embed(options.code_query)
        code_hits = _rank_nodes(
            [node for node in nodes if node.get("kind") == "CodeNode"],
            edges,
            query_vector,
            embedding_key="code_embedding",
            limit=options.limit,
        )

    store.close()
    return SessionGraphQueryResult(
        ok=True,
        graph_path=str(graph_path),
        text_hits=text_hits,
        code_hits=code_hits,
        models=models,
        diagnostics=[],
    )

def _rank_nodes(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    query_vector: list[float],
    *,
    embedding_key: str,
    limit: int,
) -> list[SessionGraphSearchHit]:
    ranked: list[tuple[float, dict[str, Any]]] = []
    for node in nodes:
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        vector = metadata.get(embedding_key)
        if not isinstance(vector, list):
            continue
        score = _cosine(query_vector, [float(x) for x in vector])
        if score > 0:
            ranked.append((score, node))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [
        SessionGraphSearchHit(
            node_id=str(node.get("id") or ""),
            kind=str(node.get("kind") or ""),
            label=str(node.get("label") or ""),
            summary=str(node.get("summary") or ""),
            score=round(score, 6),
            evidence_id=str(node.get("evidence_id") or ""),
            commit_id=str(node.get("commit_id") or ""),
            neighbors=_neighbors_for(str(node.get("id") or ""), nodes, edges),
        )
        for score, node in ranked[:limit]
    ]


def _neighbors_for(node_id: str, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {str(node.get("id") or ""): node for node in nodes}
    result: list[dict[str, Any]] = []
    for edge in edges:
        other_id = ""
        direction = ""
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if source_id == node_id:
            other_id = target_id
            direction = "out"
        elif target_id == node_id:
            other_id = source_id
            direction = "in"
        else:
            continue
        other = by_id.get(other_id)
        result.append(
            {
                "direction": direction,
                "edge_kind": edge.get("kind") or "",
                "node_id": other_id,
                "node_kind": other.get("kind") if other else "",
                "label": other.get("label") if other else "",
            }
        )
    return result[:20]


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


__all__ = ["query_session_graph"]
