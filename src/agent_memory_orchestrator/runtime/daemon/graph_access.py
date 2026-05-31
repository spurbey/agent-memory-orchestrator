from __future__ import annotations

from dataclasses import replace

from ...core.config import Settings
from ...graph.service import GraphRagService
from ...graph.store import KuzuGraphStore
from ...infrastructure.kuzu.central_graph import repo_central_graph_path


def read_graph_service(settings: Settings, *, repo_id: str = "") -> GraphRagService:
    safe_repo_id = str(repo_id or "").strip()
    if safe_repo_id:
        central_graph_path = repo_central_graph_path(settings, safe_repo_id)
        graph_settings = replace(settings, graph_path=central_graph_path)
        return GraphRagService(
            graph_settings,
            store=KuzuGraphStore(central_graph_path, read_only=True),
            read_only=True,
        )
    return GraphRagService(settings, read_only=True)
