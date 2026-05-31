"""Daemon health and metrics routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....memory import MemoryService
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore

HEALTH_ROUTES = ("/health", "/metrics")

JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_health_get(*, path: str, settings: Settings, write_json: JsonWriter) -> bool:
    """Handle daemon health and memory metrics GET routes."""
    if path == "/health":
        job_store = ProductionSessionJobStore(settings)
        try:
            reset_marker = job_store.marker()
        finally:
            job_store.close()
        write_json(
            200,
            {
                "ok": True,
                "service": "agent-memory-orchestrator",
                "graph_backend": settings.graph_backend,
                "graph_path": str(settings.graph_path),
                "qwen_runtime": settings.qwen_runtime,
                "qwen_model": settings.qwen_model,
                "qwen_timeout_seconds": settings.qwen_timeout_seconds,
                "qwen_planner_timeout_seconds": settings.qwen_planner_timeout_seconds,
                "qwen_extract_timeout_seconds": settings.qwen_extract_timeout_seconds,
                "qwen_compress_timeout_seconds": settings.qwen_compress_timeout_seconds,
                "qwen_num_ctx": settings.qwen_num_ctx,
                "drain_max_windows_per_run": settings.drain_max_windows_per_run,
                "auto_drain_enabled": settings.auto_drain_enabled,
                "auto_drain_interval_seconds": settings.auto_drain_interval_seconds,
                "auto_drain_record_limit": settings.auto_drain_record_limit,
                "auto_embedding_batch_size": settings.auto_embedding_batch_size,
                "production_marker": reset_marker,
            },
        )
        return True

    if path == "/metrics":
        svc = MemoryService(settings)
        try:
            svc.init_db()
            write_json(200, svc.inspect_metrics())
        finally:
            svc.close()
        return True

    return False


__all__ = ["HEALTH_ROUTES", "handle_health_get"]
