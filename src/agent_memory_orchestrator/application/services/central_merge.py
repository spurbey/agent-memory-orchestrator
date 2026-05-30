from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...graph.store import GraphStore
from ...reasoning_graph.central_merge.applier import apply_merge_plan
from ...reasoning_graph.central_merge.applier import repo_central_graph_path
from ...reasoning_graph.central_merge.planner import build_dry_run_merge_plan
from ...reasoning_graph.jobs import ProductionSessionJobStore

__all__ = ["CentralMergeService", "apply_merge_plan", "build_dry_run_merge_plan", "repo_central_graph_path"]


class CentralMergeService:
    """Application boundary for central graph planning and apply operations."""

    def __init__(self, settings: Settings, *, store: ProductionSessionJobStore | None = None) -> None:
        self.settings = settings
        self.store = store

    def central_graph_path(self, repo_id: str) -> Path:
        return repo_central_graph_path(self.settings, repo_id)

    def apply_plan(
        self,
        *,
        plan_id: str,
        graph_store: GraphStore | None = None,
        branch: str = "main",
        mode: str = "active",
        lock_owner: str | None = None,
    ) -> dict[str, Any]:
        return apply_merge_plan(
            settings=self.settings,
            plan_id=plan_id,
            store=self.store,
            graph_store=graph_store,
            branch=branch,
            mode=mode,
            lock_owner=lock_owner,
        )
