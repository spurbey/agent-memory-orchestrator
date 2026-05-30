from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any, ContextManager

from ...core.config import Settings
from ...graph.store import GraphStore
from ...graph.store import KuzuGraphStore
from ...reasoning_graph.jobs import PRODUCTION_STAGES
from ...reasoning_graph.jobs import ProductionSessionJobRunner
from ...reasoning_graph.jobs import ProductionSessionJobStore
from ...reasoning_graph.jobs import stage_display_name

__all__ = [
    "PRODUCTION_STAGES",
    "ProductionPipelineService",
    "ProductionSessionJobRunner",
    "ProductionSessionJobStore",
    "stage_display_name",
]


class ProductionPipelineService:
    """Application boundary for closed-session production job execution."""

    def __init__(
        self,
        settings: Settings,
        *,
        job_store: ProductionSessionJobStore | None = None,
        graph_store_factory: Callable[[Path], GraphStore] = KuzuGraphStore,
        stage_lock_factory: Callable[[str], ContextManager[Any]] | None = None,
    ) -> None:
        self.runner = ProductionSessionJobRunner(
            settings,
            job_store=job_store,
            graph_store_factory=graph_store_factory,
            stage_lock_factory=stage_lock_factory or (lambda _stage: nullcontext()),
        )

    def run_next(self, *, lease_seconds: int = 300) -> dict[str, Any]:
        return self.runner.run_next(lease_seconds=lease_seconds)

    def close(self) -> None:
        self.runner.close()

    def __enter__(self) -> "ProductionPipelineService":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
