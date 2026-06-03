from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .build import build_session_graph
from .models import SessionGraphBuildOptions
from .models import SessionGraphQueryOptions
from .query import query_session_graph


def build_and_query_session_graph(
    build_options: SessionGraphBuildOptions,
    *,
    query: str | None = None,
    code_query: str | None = None,
    limit: int = 8,
) -> dict[str, Any]:
    build_result = build_session_graph(build_options)
    query_result = query_session_graph(
        SessionGraphQueryOptions(
            graph_path=build_options.graph_path,
            query=query,
            code_query=code_query,
            text_embedding_model=build_options.text_embedding_model,
            code_embedding_model=build_options.code_embedding_model,
            limit=limit,
        )
    )
    return {
        "ok": build_result.ok and query_result.ok,
        "build": asdict(build_result),
        "query": asdict(query_result),
    }


__all__ = ["build_and_query_session_graph"]
