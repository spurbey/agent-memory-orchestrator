from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....domain.retrieval.policy import _sanitize_output_node
from ....infrastructure.kuzu import GraphStore
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from ..session.detail import _evidence_roots
from ..session.detail import _load_evidence_records
from ..session.detail import _load_session_evidence_records
from ..session.detail import _reconstruct_clean_windows
from ..session.detail import _session_pending_summary
from ..session.detail import _timeline_row
from .repo_scope import node_repo_id
from .repo_scope import node_repo_path
from .repo_scope import repo_id_for_path


def session_overview(
    *,
    settings: Settings,
    graph_store: GraphStore,
    merge_status: Callable[..., dict[str, Any]],
    limit: int = 25,
    repo_id: str = "",
) -> dict[str, Any]:
    safe_limit = max(1, min(100, int(limit)))
    safe_repo_id = str(repo_id or "").strip()
    records = _load_evidence_records(_evidence_roots(settings), limit=5000)
    jobs_by_session = jobs_by_session_map(settings, limit=5000)
    repo_cache: dict[str, str] = {}
    sessions: dict[str, dict[str, Any]] = {}
    for record in records:
        session_id = str(record.get("session_id") or "default")
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        row = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "source_apps": set(),
                "raw_events": 0,
                "event_counts": {},
                "latest_at": "",
                "first_at": "",
                "cwd": "",
                "repo": "",
                "repo_id": "",
                "branch": "",
                "latest_event": "",
            },
        )
        row["raw_events"] += 1
        row["source_apps"].add(str(record.get("source_app") or "unknown"))
        event_name = str(record.get("event_name") or "message")
        row["event_counts"][event_name] = int(row["event_counts"].get(event_name, 0)) + 1
        created_at = str(record.get("created_at") or "")
        if not row["first_at"] or created_at < row["first_at"]:
            row["first_at"] = created_at
        if not row["latest_at"] or created_at >= row["latest_at"]:
            row["latest_at"] = created_at
            row["latest_event"] = event_name
            row["cwd"] = str(payload.get("cwd") or row.get("cwd") or "")
            git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
            row["repo"] = str(git.get("repo_root") or payload.get("repo_root") or row.get("repo") or "")
            row["repo_id"] = repo_id_for_path(row["repo"] or row["cwd"], repo_cache)
            row["branch"] = str(git.get("branch") or row.get("branch") or "")

    contexts: dict[str, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for session_id, row in sessions.items():
        job = jobs_by_session.get(session_id, {})
        effective_repo_id = str(job.get("repo_id") or row.get("repo_id") or "")
        if not effective_repo_id:
            effective_repo_id = repo_id_for_path(str(row.get("repo") or row.get("cwd") or ""), repo_cache)
        if safe_repo_id and effective_repo_id != safe_repo_id:
            continue
        context = contexts.get(session_id)
        counts = graph_store.merge_status(session_id=session_id).get("counts", {})
        rows.append(
            {
                **{key: value for key, value in row.items() if key != "source_apps"},
                "repo_id": effective_repo_id,
                "repo_path": str(job.get("repo_path") or row.get("repo") or row.get("cwd") or ""),
                "source_apps": sorted(row["source_apps"]),
                "graph_counts": counts,
                "latest_context": context,
            }
        )
    rows.sort(key=lambda item: str(item.get("latest_at") or ""), reverse=True)
    return {
        "ok": True,
        "repo_id": safe_repo_id,
        "graph_status": merge_status(),
        "sessions": rows[:safe_limit],
    }


def list_repositories(*, settings: Settings, graph_store: GraphStore, limit: int = 200) -> dict[str, Any]:
    repos: dict[str, dict[str, Any]] = {}

    def add(repo_id: str, repo_path: str = "", *, source: str = "", updated_at: str = "", job_count: int = 0, plan_count: int = 0) -> None:
        key = str(repo_id or "").strip()
        if not key:
            return
        row = repos.setdefault(
            key,
            {"repo_id": key, "repo_path": "", "sources": set(), "job_count": 0, "plan_count": 0, "node_count": 0, "updated_at": ""},
        )
        if repo_path and not row["repo_path"]:
            row["repo_path"] = repo_path
        if source:
            row["sources"].add(source)
        row["job_count"] += int(job_count)
        row["plan_count"] += int(plan_count)
        if updated_at and updated_at > str(row["updated_at"]):
            row["updated_at"] = updated_at

    job_store = ProductionSessionJobStore(settings)
    try:
        for repo in job_store.list_repositories(limit=limit):
            add(
                str(repo.get("repo_id") or ""),
                str(repo.get("repo_path") or ""),
                source="jobs",
                updated_at=str(repo.get("updated_at") or ""),
                job_count=int(repo.get("job_count") or 0),
                plan_count=int(repo.get("plan_count") or 0),
            )
    finally:
        job_store.close()
    for node in graph_store.list_nodes(limit=10000, kinds=["KnowledgeAtom", "KnowledgeVersion", "GraphView"]):
        current_repo_id = node_repo_id(node)
        if not current_repo_id:
            continue
        add(current_repo_id, node_repo_path(node), source="central_graph")
        repos[current_repo_id]["node_count"] += 1
    out = []
    for row in repos.values():
        out.append({**row, "sources": sorted(row["sources"])})
    out.sort(key=lambda item: (str(item.get("updated_at") or ""), int(item.get("node_count") or 0)), reverse=True)
    return {"ok": True, "repos": out[: max(1, int(limit))]}


def jobs_by_session_map(settings: Settings, *, limit: int = 5000) -> dict[str, dict[str, Any]]:
    job_store = ProductionSessionJobStore(settings)
    try:
        return {str(job.get("session_id") or ""): job for job in job_store.list_jobs(limit=limit) if job.get("session_id")}
    finally:
        job_store.close()


def session_detail(
    *,
    settings: Settings,
    graph_store: GraphStore,
    current_context: Callable[..., dict[str, Any]],
    merge_status: Callable[..., dict[str, Any]],
    central_graph: Callable[..., dict[str, Any]],
    session_id: str,
    limit: int = 120,
) -> dict[str, Any]:
    session_id = str(session_id or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    safe_limit = max(1, min(500, int(limit)))
    records, evidence_source = _load_session_evidence_records(settings, session_id=session_id, limit=safe_limit)
    nodes = [_sanitize_output_node(node) for node in graph_store.list_nodes(session_id=session_id, limit=300)]
    edges = graph_store.list_edges(session_id=session_id, limit=500)
    pending = _session_pending_summary(settings, session_id=session_id)
    windows = _reconstruct_clean_windows(records, nodes)
    return {
        "ok": True,
        "session_id": session_id,
        "timeline": [_timeline_row(record) for record in records],
        "windows": windows,
        "current_context": current_context(session_id=session_id, limit=5),
        "merge_status": merge_status(session_id=session_id),
        "pending": {"count": pending.get("count", 0), "cursor_path": pending.get("cursor_path"), "source": pending.get("source")},
        "evidence_source": evidence_source,
        "graph": {
            "nodes": nodes,
            "edges": edges,
        },
        "central_graph": central_graph(limit=80),
    }


__all__ = ["jobs_by_session_map", "list_repositories", "session_detail", "session_overview"]
