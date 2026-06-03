"""Daemon retrieval routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ..coordination import bounded_int
from ..graph_access import read_graph_service
from ..payloads import optional_payload_path
from ..payloads import settings_with_payload_paths

RETRIEVAL_ROUTES = (
    "/api/retrieval-runs",
    "/api/retrieval-runs/{run_id}",
    "/graph/retrieval-build",
    "/graph/retrieval-embed",
    "/graph/retrieve",
)

JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_graph_retrieval_post(
    *,
    path: str,
    payload: dict[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle production graph retrieval index/read POST routes."""
    if path == "/graph/retrieval-build":
        graph_settings = settings_with_payload_paths(settings, payload, prefer_retrieval=True)
        graph = read_graph_service(graph_settings)
        try:
            limit = bounded_int(str(payload.get("limit") or ""), default=10000, minimum=1, maximum=100000)
            max_doc_chars = bounded_int(
                str(payload.get("max_doc_chars") or ""),
                default=5000,
                minimum=1000,
                maximum=50000,
            )
            result = graph.rebuild_retrieval_index(
                db_path=optional_payload_path(payload, "db_path"),
                session_id=str(payload.get("session_id") or ""),
                repo_id=str(payload.get("repo_id") or ""),
                limit=limit,
                max_doc_chars=max_doc_chars,
            )
            write_json(200, result)
        finally:
            graph.close()
        return True

    if path == "/graph/retrieval-embed":
        graph_settings = settings_with_payload_paths(settings, payload, prefer_retrieval=True)
        graph = read_graph_service(graph_settings)
        try:
            limit = bounded_int(str(payload.get("limit") or ""), default=100, minimum=0, maximum=100000)
            result = graph.embed_retrieval_index(
                db_path=optional_payload_path(payload, "db_path"),
                session_id=str(payload.get("session_id") or ""),
                repo_id=str(payload.get("repo_id") or ""),
                limit=limit,
                model=str(payload.get("model") or ""),
                graph_scope=str(payload.get("graph_scope") or ""),
                rebuild_faiss=bool(payload.get("rebuild_faiss", True)),
            )
            write_json(200, result)
        finally:
            graph.close()
        return True

    if path == "/graph/retrieve":
        graph_settings = settings_with_payload_paths(settings, payload, prefer_retrieval=True)
        graph = read_graph_service(graph_settings, repo_id=str(payload.get("repo_id") or ""))
        try:
            limit = bounded_int(str(payload.get("limit") or ""), default=8, minimum=1, maximum=50)
            try:
                result = graph.retrieve_indexed_graph(
                    query=str(payload.get("query") or ""),
                    db_path=optional_payload_path(payload, "db_path"),
                    session_id=str(payload.get("session_id") or ""),
                    repo_id=str(payload.get("repo_id") or ""),
                    limit=limit,
                    use_vector=bool(payload.get("use_vector", True)),
                    model=str(payload.get("model") or ""),
                    graph_scope=str(payload.get("graph_scope") or ""),
                    require_vector=bool(payload.get("require_vector", False)),
                    include_answer=bool(payload.get("include_answer", True)),
                )
            except ValueError as exc:
                result = {
                    "ok": False,
                    "error": str(exc),
                    "hint": "Build the production retrieval index and embeddings for the configured graph, or configure retrieval_graph_path/retrieval_db_path.",
                    "graph_path": str(graph_settings.graph_path),
                    "db_path": str(graph_settings.retrieval_db_path),
                }
            write_json(200, result)
        finally:
            graph.close()
        return True

    return False


__all__ = ["RETRIEVAL_ROUTES", "handle_graph_retrieval_post"]
