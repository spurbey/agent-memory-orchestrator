"""Daemon production job routes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import unquote

from ....core.config import Settings
from ....reasoning_graph.jobs import ProductionSessionJobStore
from ..coordination import bounded_int

JOB_ROUTES = ("/api/jobs", "/api/jobs/{job_id}", "/api/jobs/{job_id}/retry")

JsonWriter = Callable[[int, dict[str, Any]], bool]


def handle_jobs_get(
    *,
    path: str,
    query: dict[str, list[str]],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle production job list/detail GET routes."""
    if path != "/api/jobs" and not path.startswith("/api/jobs/"):
        return False

    raw_limit = (query.get("limit") or ["100"])[0]
    limit = bounded_int(raw_limit, default=100, minimum=1, maximum=500)
    job_store = ProductionSessionJobStore(settings)
    try:
        if path == "/api/jobs":
            repo_id = (query.get("repo_id") or [""])[0]
            write_json(
                200,
                {
                    "ok": True,
                    "repo_id": repo_id,
                    "jobs": job_store.list_jobs(limit=limit, repo_id=repo_id),
                    "reset_marker": job_store.marker(),
                },
            )
            return True

        job_id = unquote(path.removeprefix("/api/jobs/").strip("/"))
        job = job_store.get_job(job_id)
        if job is None:
            write_json(404, {"ok": False, "error": "job not found"})
            return True
        write_json(
            200,
            {
                "ok": True,
                "job": job,
                "stages": job_store.list_stages(job_id),
                "events": job_store.list_events(job_id, limit=limit),
                "reset_marker": job_store.marker(),
            },
        )
    finally:
        job_store.close()
    return True


def handle_job_retry_post(
    *,
    path: str,
    payload: dict[str, Any],
    settings: Settings,
    write_json: JsonWriter,
) -> bool:
    """Handle production job retry POST route."""
    if not path.startswith("/api/jobs/") or not path.endswith("/retry"):
        return False

    job_id = unquote(path.removeprefix("/api/jobs/").removesuffix("/retry").strip("/"))
    job_store = ProductionSessionJobStore(settings)
    try:
        job = job_store.retry_job(job_id, forced_by=str(payload.get("forced_by") or "daemon-api"))
        write_json(200, {"ok": True, "job": job})
    finally:
        job_store.close()
    return True


__all__ = ["JOB_ROUTES", "handle_job_retry_post", "handle_jobs_get"]
