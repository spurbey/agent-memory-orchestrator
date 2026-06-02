from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ....core.config import Settings

from .production_eval_cases import _cases
from .production_eval_cases import _compact_quality
from .production_eval_cases import _quality_issue_codes
from .production_eval_retrieval import _faiss_state
from .production_eval_retrieval import _retrieval_query_gates
from .production_eval_retrieval import _retrieval_state
from .production_eval_storage import _job_row
from .production_eval_storage import _query
from .production_eval_storage import _read_json
from .production_eval_storage import _select
from .production_eval_storage import _stage_json
from .production_eval_storage import _stage_rows
from .production_eval_storage import stable_hash


DEFAULT_TARGET_JOB_ID = "v2job:0b68249f48c244c68fb12977eb93d9ba"
DEFAULT_TARGET_REPO_ID = "repo:remote:311ebb9cda1fb40f"


__all__ = [
    "DEFAULT_TARGET_JOB_ID",
    "DEFAULT_TARGET_REPO_ID",
    "_cases",
    "_faiss_state",
    "_retrieval_query_gates",
    "default_production_eval_path",
    "run_production_semantic_eval",
    "stable_hash",
]


def run_production_semantic_eval(
    settings: Settings,
    *,
    job_id: str = DEFAULT_TARGET_JOB_ID,
    repo_id: str = "",
    mode: str = "baseline",
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Read-only production semantic state report for curated central memory.

    This deliberately avoids job-store/retrieval-index helpers because those
    constructors initialize schemas. The baseline report must be safe to run
    before regeneration and without mutating production state.
    """

    job = _job_row(settings.db_path, job_id)
    if not job and mode != "post_apply":
        raise ValueError(f"unknown_job:{job_id}")
    safe_repo_id = str(repo_id or job.get("repo_id") or DEFAULT_TARGET_REPO_ID).strip()
    stages = _stage_rows(settings.db_path, job_id)
    by_stage = {str(stage.get("stage") or ""): stage for stage in stages}
    artifact_dir = Path(str(job.get("artifact_dir") or ""))
    kuzu_write = _kuzu_write_state(artifact_dir)
    central = _central_state(settings, job_id=job_id, repo_id=safe_repo_id)
    retrieval = _retrieval_state(settings.retrieval_db_path, repo_id=safe_repo_id, settings=settings)
    quality = _compact_quality(_stage_json(by_stage.get("quality_eval", {})))
    cases = _cases(
        mode=mode,
        kuzu_write=kuzu_write,
        central=central,
        retrieval=retrieval,
        quality=quality if isinstance(quality, dict) else {},
    )
    blockers = list(
        dict.fromkeys(
            [
                *(failure for case in cases for failure in case["blocking_failures"]),
                *_quality_issue_codes(quality if isinstance(quality, dict) else {}),
            ]
        )
    )
    payload = {
        "report_version": "production-semantic-eval-v1",
        "mode": mode,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "job_id": job_id,
        "repo_id": safe_repo_id,
        "product_ready": not blockers,
        "blocked_issues": blockers,
        "job": _select(job, ("job_id", "session_id", "status", "current_stage", "last_successful_stage", "repo_id", "repo_path")),
        "stages": [_select(stage, ("stage", "status", "input_hash", "output_hash", "stage_config_hash", "output_artifact")) for stage in stages],
        "kuzu_write": kuzu_write,
        "central_version_merge": central,
        "retrieval": retrieval,
        "quality_eval": quality,
        "cases": cases,
    }
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        payload["path"] = str(out_path)
    return payload


def default_production_eval_path(root: Path | None = None) -> Path:
    base = root or Path.cwd()
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-central-memory-production")
    return base / ".tmp" / "validation-evals" / stamp / "semantic_input_output_eval.json"

def _central_state(settings: Settings, *, job_id: str, repo_id: str) -> dict[str, Any]:
    db_state = _central_db_state(settings.db_path, job_id=job_id, repo_id=repo_id)
    graph_state = _central_graph_state(settings, repo_id=repo_id)
    if graph_state.get("applied"):
        merged = {**db_state, **graph_state}
        merged["db_job_state"] = db_state
        return merged
    return {**db_state, "repo_central_graph": graph_state}


def _central_db_state(db_path: Path, *, job_id: str, repo_id: str) -> dict[str, Any]:
    plans = _query(db_path, "SELECT * FROM v2_central_merge_plans WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1", (job_id,))
    plan = plans[0] if plans else {}
    plan_id = str(plan.get("plan_id") or "")
    commits = _query(db_path, "SELECT * FROM v2_graph_commits WHERE plan_id = ? ORDER BY updated_at DESC LIMIT 1", (plan_id,)) if plan_id else []
    commit = commits[0] if commits else {}
    views = _query(
        db_path,
        "SELECT * FROM v2_graph_views WHERE repo_id = ? AND branch = 'main' AND mode = 'active' AND status = 'active' ORDER BY updated_at DESC LIMIT 1",
        (repo_id,),
    )
    view = views[0] if views else {}
    graph_commit_status = str(commit.get("status") or "")
    active_head = str(view.get("graph_commit_id") or "")
    return {
        "available": bool(plan),
        "plan_id": plan_id,
        "plan_status": str(plan.get("status") or ""),
        "plan_mode": str(plan.get("mode") or ""),
        "plan_hash": str(plan.get("plan_hash") or ""),
        "repo_id": repo_id,
        "graph_commit_id": str(commit.get("graph_commit_id") or ""),
        "graph_commit_status": graph_commit_status,
        "active_graph_view_id": str(view.get("view_id") or ""),
        "active_graph_view_head": active_head,
        "applied": graph_commit_status == "applied" and bool(active_head),
        "source": "sqlite_job_rows",
    }


def _central_graph_state(settings: Settings, *, repo_id: str) -> dict[str, Any]:
    graph_path = _repo_central_graph_path(settings, repo_id)
    state: dict[str, Any] = {
        "source": "repo_central_graph",
        "central_graph_path": str(graph_path),
        "available": False,
        "applied": False,
        "repo_id": repo_id,
    }
    if not graph_path.exists():
        return {**state, "error": "central_graph_missing"}
    try:
        import kuzu  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - environment dependent
        return {**state, "error": f"kuzu_unavailable:{exc}"}
    try:
        database = kuzu.Database(str(graph_path), read_only=True)
        conn = kuzu.Connection(database)
        views = _kuzu_query(
            conn,
            """
            MATCH (n:GraphNode)
            WHERE n.kind = 'GraphView' AND n.status = 'active'
            RETURN n.id, n.label, n.status, n.metadata_json
            LIMIT 50;
            """,
        )
        view = _select_active_graph_view(views, repo_id=repo_id)
        if not view:
            return {
                **state,
                "available": True,
                "active_graph_view_id": "",
                "active_graph_view_head": "",
                "error": "active_graph_view_missing",
                "node_kind_counts": _kuzu_count_by(conn, node=True),
                "edge_kind_counts": _kuzu_count_by(conn, node=False),
            }
        metadata = view.get("metadata") if isinstance(view.get("metadata"), dict) else {}
        active_head = str(metadata.get("graph_commit_id") or view.get("graph_commit_id") or "")
        commit = _kuzu_graph_commit(conn, active_head) if active_head else {}
        commit_metadata = commit.get("metadata") if isinstance(commit.get("metadata"), dict) else {}
        graph_commit_status = str(commit.get("status") or commit_metadata.get("status") or "")
        applied = graph_commit_status == "applied" and bool(active_head)
        return {
            **state,
            "available": True,
            "plan_id": str(metadata.get("merge_plan_id") or commit_metadata.get("merge_plan_id") or ""),
            "plan_status": "applied" if applied else "",
            "plan_mode": str(metadata.get("mode") or "repo_active_graph_view"),
            "plan_hash": str(commit_metadata.get("plan_hash") or metadata.get("plan_hash") or ""),
            "graph_commit_id": active_head,
            "graph_commit_status": graph_commit_status,
            "active_graph_view_id": str(view.get("id") or ""),
            "active_graph_view_head": active_head,
            "applied": applied,
            "node_kind_counts": _kuzu_count_by(conn, node=True),
            "edge_kind_counts": _kuzu_count_by(conn, node=False),
            "knowledge_version_status_counts": _kuzu_knowledge_version_status_counts(conn),
        }
    except Exception as exc:  # pragma: no cover - depends on live Kuzu availability
        return {**state, "available": graph_path.exists(), "error": f"{type(exc).__name__}:{exc}"}


def _repo_central_graph_path(settings: Settings, repo_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(repo_id or "unknown")).strip("._-") or "unknown"
    return settings.home / ".graph" / "central" / safe / "central.kuzu"


def _select_active_graph_view(rows: list[dict[str, Any]], *, repo_id: str) -> dict[str, Any]:
    fallback: dict[str, Any] = {}
    for row in rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        if not fallback:
            fallback = row
        if (
            str(metadata.get("repo_id") or "").strip() == repo_id
            and str(metadata.get("branch") or "main") == "main"
            and str(metadata.get("mode") or "active") == "active"
        ):
            return row
    return fallback


def _kuzu_graph_commit(conn: Any, graph_commit_id: str) -> dict[str, Any]:
    if not graph_commit_id:
        return {}
    rows = _kuzu_query(
        conn,
        f"""
        MATCH (n:GraphNode)
        WHERE n.kind = 'GraphCommit' AND n.id = '{_kuzu_string(graph_commit_id)}'
        RETURN n.id, n.label, n.status, n.metadata_json
        LIMIT 1;
        """,
    )
    return rows[0] if rows else {}


def _kuzu_count_by(conn: Any, *, node: bool) -> dict[str, int]:
    query = (
        """
        MATCH (n:GraphNode)
        RETURN n.kind, count(*)
        ORDER BY count(*) DESC;
        """
        if node
        else """
        MATCH ()-[e:GraphEdge]->()
        RETURN e.kind, count(*)
        ORDER BY count(*) DESC;
        """
    )
    out: dict[str, int] = {}
    for row in _kuzu_query(conn, query):
        key = str(row.get("kind") or "").strip()
        if key:
            out[key] = int(row.get("count") or 0)
    return out


def _kuzu_knowledge_version_status_counts(conn: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    rows = _kuzu_query(
        conn,
        """
        MATCH (n:GraphNode)
        WHERE n.kind = 'KnowledgeVersion'
        RETURN n.status, count(*)
        ORDER BY count(*) DESC;
        """,
    )
    for row in rows:
        status = str(row.get("status") or "").strip()
        if status:
            out[status] = int(row.get("count") or 0)
    return out


def _kuzu_query(conn: Any, query: str) -> list[dict[str, Any]]:
    result = conn.execute(query)
    rows: list[dict[str, Any]] = []
    while result.has_next():
        values = result.get_next()
        if not isinstance(values, (list, tuple)):
            values = [values]
        rows.append(_decode_kuzu_values(values))
    return rows


def _decode_kuzu_values(values: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    if len(values) >= 4:
        metadata = _json_object(values[3])
        return {
            "id": str(values[0] or ""),
            "label": str(values[1] or ""),
            "status": str(values[2] or ""),
            "metadata_json": str(values[3] or ""),
            "metadata": metadata,
            "graph_commit_id": str(metadata.get("graph_commit_id") or ""),
        }
    if len(values) >= 2:
        return {"kind": str(values[0] or ""), "status": str(values[0] or ""), "count": int(values[1] or 0)}
    return {}


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        payload = json.loads(str(value or "{}"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _kuzu_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def _kuzu_write_state(artifact_dir: Path) -> dict[str, Any]:
    root = artifact_dir / "kuzu_write"
    compact = root / "compact_graph_manifest.json"
    curated = root / "curated_graph_manifest.json"
    audit = root / "curation_audit.json"
    result = root / "kuzu_write_result.json"
    return {
        "artifact_dir": str(root),
        "compact_manifest_path": str(compact),
        "compact_manifest_exists": compact.exists(),
        "compact_manifest_size": compact.stat().st_size if compact.exists() else 0,
        "curated_manifest_path": str(curated),
        "curated_manifest_exists": curated.exists(),
        "curated_manifest_size": curated.stat().st_size if curated.exists() else 0,
        "curation_audit_path": str(audit),
        "curation_audit_exists": audit.exists(),
        "kuzu_write_result": _read_json(result),
    }
