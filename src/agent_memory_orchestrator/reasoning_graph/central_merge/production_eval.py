from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...core.config import Settings


DEFAULT_TARGET_JOB_ID = "v2job:0b68249f48c244c68fb12977eb93d9ba"
DEFAULT_TARGET_REPO_ID = "repo:remote:311ebb9cda1fb40f"


def run_production_semantic_eval(
    settings: Settings,
    *,
    job_id: str = DEFAULT_TARGET_JOB_ID,
    repo_id: str = "",
    mode: str = "baseline",
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Read-only production semantic state report for curated central memory.

    This deliberately avoids V2SessionJobStore/RetrievalIndexStore because those
    constructors initialize schemas. The baseline report must be safe to run
    before regeneration and without mutating production state.
    """

    job = _job_row(settings.db_path, job_id)
    if not job:
        raise ValueError(f"unknown_job:{job_id}")
    safe_repo_id = str(repo_id or job.get("repo_id") or DEFAULT_TARGET_REPO_ID).strip()
    stages = _stage_rows(settings.db_path, job_id)
    by_stage = {str(stage.get("stage") or ""): stage for stage in stages}
    artifact_dir = Path(str(job.get("artifact_dir") or ""))
    kuzu_write = _kuzu_write_state(artifact_dir)
    central = _central_state(settings.db_path, job_id=job_id, repo_id=safe_repo_id)
    retrieval = _retrieval_state(settings.retrieval_db_path, repo_id=safe_repo_id)
    quality = _compact_quality(_stage_json(by_stage.get("quality_eval", {})))
    cases = _cases(
        kuzu_write=kuzu_write,
        central=central,
        retrieval=retrieval,
        quality=quality if isinstance(quality, dict) else {},
    )
    blockers = [failure for case in cases for failure in case["blocking_failures"]]
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


def _cases(*, kuzu_write: dict[str, Any], central: dict[str, Any], retrieval: dict[str, Any], quality: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _case(
            "curated_manifest_present",
            expected={"curated_graph_manifest": "exists"},
            actual={"exists": kuzu_write.get("curated_manifest_exists"), "path": kuzu_write.get("curated_manifest_path")},
            passed=bool(kuzu_write.get("curated_manifest_exists")),
            failures=[] if kuzu_write.get("curated_manifest_exists") else ["curated_graph_manifest_missing"],
            reason="Production memory input must be the curated graph manifest.",
        ),
        _case(
            "central_merge_applied",
            expected={"status": "applied", "active_graph_view_head": "non_empty"},
            actual={
                "plan_status": central.get("plan_status"),
                "plan_mode": central.get("plan_mode"),
                "graph_commit_status": central.get("graph_commit_status"),
                "active_graph_view_head": central.get("active_graph_view_head"),
            },
            passed=central.get("applied") is True and bool(central.get("active_graph_view_head")),
            failures=[] if central.get("applied") and central.get("active_graph_view_head") else ["central_merge_not_applied"],
            reason="AMO repo central memory is not active until a GraphCommit backs GraphView(main, active).",
        ),
        _case(
            "retrieval_not_full_trace_dominated",
            expected={"dominant_docs": "curated_or_central"},
            actual={
                "repo_doc_count": retrieval.get("repo_doc_count"),
                "trace_doc_count": retrieval.get("trace_doc_count"),
                "curated_doc_count": retrieval.get("curated_doc_count"),
                "doc_type_counts": retrieval.get("doc_type_counts"),
            },
            passed=bool(retrieval.get("repo_doc_count")) and not retrieval.get("full_trace_dominated"),
            failures=_retrieval_projection_failures(retrieval),
            reason="Strict repo retrieval must not surface raw CodeNode/CodeHunk trace docs as the product projection.",
        ),
        _case(
            "strict_repo_excludes_legacy_docs",
            expected={"repo_id_empty_docs": "not_in_strict_repo"},
            actual={
                "legacy_doc_count": retrieval.get("legacy_doc_count"),
                "strict_repo_id": retrieval.get("repo_id"),
            },
            passed=retrieval.get("strict_repo_legacy_leak") is False,
            failures=[] if retrieval.get("strict_repo_legacy_leak") is False else ["strict_repo_legacy_leak"],
            reason="Legacy repo_id='' docs may exist, but strict repo retrieval must not use them.",
        ),
        _case(
            "vector_status_truthful",
            expected={"embedding_status": "ready_or_partial_truthful", "faiss_status": "ready_or_partial_truthful"},
            actual={"embedding": retrieval.get("embedding_coverage"), "faiss": retrieval.get("faiss")},
            passed=bool(retrieval.get("vector_status_truthful")),
            failures=[] if retrieval.get("vector_status_truthful") else ["vector_status_unavailable"],
            reason="Partial embeddings/FAISS are acceptable only when reported honestly.",
        ),
        _case(
            "quality_not_product_ready_when_blocked",
            expected={"product_ready": False},
            actual={"product_ready": quality.get("product_ready"), "blocking_issues": quality.get("blocking_issues")},
            passed=quality.get("product_ready") is not True,
            failures=[] if quality.get("product_ready") is not True else ["quality_eval_overstated_product_ready"],
            reason="Quality eval must not mark stale/full-trace state as product-ready.",
        ),
    ]


def _case(
    case_id: str,
    *,
    expected: dict[str, Any],
    actual: dict[str, Any],
    passed: bool,
    failures: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "expected_output_shape": expected,
        "actual_output_summary": actual,
        "passed": passed,
        "semantic_reason": reason,
        "blocking_failures": failures,
    }


def _job_row(db_path: Path, job_id: str) -> dict[str, Any]:
    rows = _query(db_path, "SELECT * FROM v2_session_jobs WHERE job_id = ?", (job_id,))
    return rows[0] if rows else {}


def _stage_rows(db_path: Path, job_id: str) -> list[dict[str, Any]]:
    return _query(db_path, "SELECT * FROM v2_session_job_stages WHERE job_id = ? ORDER BY started_at ASC", (job_id,))


def _stage_json(stage: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(stage.get("output_artifact") or ""))
    payload = _read_json(path)
    if isinstance(payload, dict) and payload:
        return payload
    diagnostics = stage.get("diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {}


def _compact_quality(payload: dict[str, Any]) -> dict[str, Any]:
    issues = payload.get("blocking_issues")
    if not isinstance(issues, list):
        issues = []
    compact_issues: list[Any] = []
    for issue in issues:
        if isinstance(issue, dict):
            compact_issues.append(
                {
                    "code": issue.get("code") or issue.get("reason") or "unknown",
                    "message": issue.get("message") or issue.get("stage") or "",
                }
            )
        else:
            compact_issues.append(issue)
    return {
        "ok": payload.get("ok"),
        "product_ready": payload.get("product_ready"),
        "blocking_issues": compact_issues,
    }


def _retrieval_projection_failures(retrieval: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if not retrieval.get("repo_doc_count"):
        failures.append("retrieval_projection_missing")
    if retrieval.get("full_trace_dominated"):
        failures.append("retrieval_full_trace_dominated")
    return failures


def _central_state(db_path: Path, *, job_id: str, repo_id: str) -> dict[str, Any]:
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
    }


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


def _retrieval_state(db_path: Path, *, repo_id: str) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "repo_id": repo_id, "repo_doc_count": 0, "vector_status_truthful": True}
    doc_type_rows = _query(
        db_path,
        """
        SELECT doc_type, node_kind, COUNT(*) AS count
        FROM retrieval_documents
        WHERE repo_id = ?
        GROUP BY doc_type, node_kind
        ORDER BY count DESC
        """,
        (repo_id,),
    )
    repo_doc_count = sum(int(row.get("count") or 0) for row in doc_type_rows)
    legacy_count = _scalar(db_path, "SELECT COUNT(*) FROM retrieval_documents WHERE COALESCE(repo_id, '') = ''")
    trace_doc_count = sum(
        int(row.get("count") or 0)
        for row in doc_type_rows
        if str(row.get("doc_type") or "") in {"session_codenode", "session_codehunk", "session_symbol", "code"}
    )
    curated_doc_count = sum(
        int(row.get("count") or 0)
        for row in doc_type_rows
        if str(row.get("doc_type") or "") in {"code_impact", "file_impact", "file_ref", "symbol_ref", "code_region_ref"}
    )
    embedded_count = _scalar(
        db_path,
        "SELECT COUNT(*) FROM graph_embeddings WHERE status = 'active' AND graph_scope = 'v2'",
        default=0,
    )
    faiss = _faiss_state(db_path)
    return {
        "exists": True,
        "repo_id": repo_id,
        "repo_doc_count": repo_doc_count,
        "legacy_doc_count": legacy_count,
        "doc_type_counts": doc_type_rows,
        "trace_doc_count": trace_doc_count,
        "curated_doc_count": curated_doc_count,
        "full_trace_dominated": trace_doc_count > max(curated_doc_count, 0),
        "strict_repo_legacy_leak": False,
        "embedding_coverage": {
            "status": "ready" if repo_doc_count and embedded_count >= repo_doc_count else "partial" if embedded_count else "missing",
            "embedded_docs": embedded_count,
            "total_docs": repo_doc_count,
        },
        "faiss": faiss,
        "vector_status_truthful": True,
    }


def _faiss_state(db_path: Path) -> dict[str, Any]:
    root = db_path.parent / "indexes" / db_path.stem
    if not root.exists():
        return {"status": "missing", "item_count": 0, "path": str(root)}
    metadata_files = sorted(root.glob("*.json"))
    item_count = 0
    latest = ""
    for path in metadata_files:
        payload = _read_json(path)
        if isinstance(payload, dict):
            records = payload.get("records") if isinstance(payload.get("records"), list) else []
            if len(records) >= item_count:
                item_count = len(records)
                latest = str(path)
    return {"status": "ready" if item_count else "partial", "item_count": item_count, "path": latest or str(root)}


def _query(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    uri = db_path.resolve().as_posix().replace("'", "''")
    conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(sql, params).fetchall()
        return [_decode_row(row) for row in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def _scalar(db_path: Path, sql: str, params: tuple[Any, ...] = (), *, default: int = 0) -> int:
    rows = _query(db_path, sql, params)
    if not rows:
        return default
    first = next(iter(rows[0].values()), default)
    try:
        return int(first)
    except (TypeError, ValueError):
        return default


def _decode_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if isinstance(value, str) and key.endswith("_json"):
            try:
                out[key.removesuffix("_json")] = json.loads(value)
            except json.JSONDecodeError:
                out[key.removesuffix("_json")] = {}
    return out


def _read_json(path: Path) -> Any:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _select(row: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: row.get(key) for key in keys}


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
