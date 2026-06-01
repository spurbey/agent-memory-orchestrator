from __future__ import annotations

import threading
import time
from typing import Any

from ...core.config import Settings
from ...application.pipeline.job_runner import ProductionSessionJobRunner
from ...application.services.graph_rag import GraphRagService
from .coordination import DRAIN_LOCK
from .coordination import GRAPH_WRITE_LOCK
from .coordination import production_stage_lock
from .logging import daemon_log


def start_auto_drain_worker(settings: Settings) -> threading.Thread | None:
    if not settings.auto_drain_enabled:
        daemon_log(settings, "auto_drain_disabled")
        return None
    worker = threading.Thread(target=_auto_drain_loop, args=(settings,), name="amo-auto-drain", daemon=True)
    worker.start()
    daemon_log(
        settings,
        "auto_drain_started",
        interval_seconds=settings.auto_drain_interval_seconds,
        embedding_batch_size=settings.auto_embedding_batch_size,
    )
    return worker


def _auto_drain_loop(settings: Settings) -> None:
    while True:
        time.sleep(settings.auto_drain_interval_seconds)
        try:
            result = run_auto_drain_once(settings)
            production_job_run = result.get("production_job_run") or {}
            if result.get("windows_processed") or result.get("records_ingested") or production_job_run.get("ran"):
                daemon_log(settings, "auto_drain_cycle", **result)
        except Exception as exc:
            daemon_log(settings, "auto_drain_failed", error_type=type(exc).__name__, error=str(exc))


def run_auto_drain_once(settings: Settings) -> dict[str, Any]:
    with DRAIN_LOCK, GRAPH_WRITE_LOCK:
        graph = GraphRagService(settings)
        try:
            drain = graph.drain_evidence(
                limit=settings.auto_drain_record_limit,
                max_windows=settings.drain_max_windows_per_run,
            )
        finally:
            graph.close()

    runner = ProductionSessionJobRunner(settings, stage_lock_factory=production_stage_lock)
    try:
        job_run = runner.run_next()
    finally:
        runner.close()
    return {
        "records_ingested": int(drain.get("records_ingested") or 0),
        "windows_processed": int(drain.get("windows_processed") or 0),
        "stopped_reason": drain.get("stopped_reason"),
        "pending_sessions": drain.get("pending_sessions"),
        "production_job_run": job_run,
    }
