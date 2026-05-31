from __future__ import annotations

import json
from typing import Any

from ...core.config import Settings
from ...infrastructure.sqlite.production_job_store import ProductionSessionJobStore


def list_repositories_fast(settings: Settings, *, limit: int = 200) -> dict[str, Any]:
    """Return dashboard repository scope from SQLite control state only.

    The dashboard must remain available while graph writes or Qwen jobs are in
    flight. Repo scope is operational metadata, so it should not require opening
    Kuzu or waiting on graph write locks.
    """

    job_store = ProductionSessionJobStore(settings)
    try:
        return {"ok": True, "repos": job_store.list_repositories(limit=limit), "source": "sqlite_control_state"}
    finally:
        job_store.close()


def session_overview_fast(settings: Settings, *, limit: int = 80, repo_id: str = "") -> dict[str, Any]:
    """Return dashboard session cards from SQLite control rows.

    The session list is a dashboard boot dependency. It must not scan Kuzu or
    raw transcript files; detailed session views can pay that cost after the
    operator selects one session.
    """

    safe_limit = max(1, min(500, int(limit)))
    safe_repo_id = str(repo_id or "").strip()
    job_store = ProductionSessionJobStore(settings)
    try:
        rows: list[dict[str, Any]] = []
        jobs = job_store.list_jobs(limit=safe_limit, repo_id=safe_repo_id)
        for job in jobs:
            diagnostics = stage_diagnostics(job_store, str(job.get("job_id") or ""), "evidence_view")
            quality = diagnostics.get("quality") if isinstance(diagnostics.get("quality"), dict) else {}
            raw_events = int(diagnostics.get("raw_record_count") or quality.get("raw_record_count") or 0)
            source_app = str(job.get("source_app") or "codex")
            rows.append(
                {
                    "session_id": str(job.get("session_id") or ""),
                    "raw_events": raw_events,
                    "source_apps": [source_app] if source_app else [],
                    "event_counts": {},
                    "first_at": str(job.get("created_at") or ""),
                    "latest_at": str(job.get("updated_at") or ""),
                    "latest_event": "closed_session",
                    "cwd": str(job.get("repo_path") or ""),
                    "repo": str(job.get("repo_path") or ""),
                    "repo_id": str(job.get("repo_id") or ""),
                    "repo_path": str(job.get("repo_path") or ""),
                    "branch": "",
                    "graph_counts": {},
                    "latest_context": None,
                }
            )
        return {
            "ok": True,
            "repo_id": safe_repo_id,
            "source": "sqlite_control_state",
            "graph_status": {"ok": True, "source": "not_loaded_for_dashboard_session_list"},
            "sessions": rows,
        }
    finally:
        job_store.close()


def stage_diagnostics(job_store: ProductionSessionJobStore, job_id: str, stage: str) -> dict[str, Any]:
    row = job_store.stage_row(job_id=job_id, stage=stage)
    if not row:
        return {}
    try:
        payload = json.loads(str(row.get("diagnostics_json") or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def graph_unavailable_payload(
    settings: Settings,
    *,
    path: str,
    repo_id: str,
    error: Exception,
    limit: int,
) -> dict[str, Any]:
    payload = {
        "ok": False,
        "backend": settings.graph_backend,
        "graph_path": str(settings.graph_path),
        "error": str(error),
        "error_type": type(error).__name__,
        "warning": "graph_temporarily_unavailable",
    }
    if path == "/api/graph/central":
        return {
            **payload,
            "repo_id": str(repo_id or ""),
            "nodes": [],
            "edges": [],
            "full": False,
            "limit": max(1, int(limit)),
            "status": payload,
            "warnings": ["graph_temporarily_unavailable"],
        }
    return payload
