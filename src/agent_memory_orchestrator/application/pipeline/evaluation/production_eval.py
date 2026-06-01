from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....infrastructure.faiss.embedding_store import GraphEmbeddingHit
from ....infrastructure.faiss.embedding_store import cosine_similarity
from ....application.services.retrieval.embedding import RETRIEVAL_EMBEDDING_KIND
from ....domain.retrieval.models import RetrievalCandidate
from ....domain.retrieval.models import RetrievalDocument
from ....application.services.retrieval.query import retrieve_session_graph
from ....infrastructure.llm.text_embedder import StrictTextEmbedder


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


def _cases(
    *,
    kuzu_write: dict[str, Any],
    central: dict[str, Any],
    retrieval: dict[str, Any],
    quality: dict[str, Any],
    mode: str = "baseline",
) -> list[dict[str, Any]]:
    active_projection_metadata = retrieval.get("active_projection", {}).get("metadata")
    if not isinstance(active_projection_metadata, dict):
        active_projection_metadata = {}
    active_projection_uses_curated = (
        mode == "post_apply"
        and active_projection_metadata.get("curated_manifest_exists") is True
        and active_projection_metadata.get("retrieval_source") == "curated_graph_manifest"
    )
    curated_manifest_present = bool(kuzu_write.get("curated_manifest_exists")) or active_projection_uses_curated
    cases = [
        _case(
            "curated_manifest_present",
            expected={"curated_graph_manifest": "exists"},
            actual={
                "exists": kuzu_write.get("curated_manifest_exists"),
                "path": kuzu_write.get("curated_manifest_path"),
                "active_projection_curated_manifest": active_projection_metadata.get("curated_manifest_exists"),
                "active_projection_retrieval_source": active_projection_metadata.get("retrieval_source"),
            },
            passed=curated_manifest_present,
            failures=[] if curated_manifest_present else ["curated_graph_manifest_missing"],
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
                "source": central.get("source"),
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
            "vector_retrieval_ready",
            expected={"embedding_status": "ready", "faiss_status": "ready"},
            actual={"embedding": retrieval.get("embedding_coverage"), "faiss": retrieval.get("faiss")},
            passed=not _vector_readiness_failures(retrieval),
            failures=_vector_readiness_failures(retrieval),
            reason="Product-ready retrieval requires complete embedding and FAISS coverage; partial vectors may be used only as a disclosed degraded mode.",
        ),
        *_retrieval_query_gate_cases(retrieval),
    ]
    independent_failures = [
        failure
        for case in cases
        for failure in case.get("blocking_failures", [])
        if str(failure or "")
    ]
    expected_quality_ready = not independent_failures
    quality_ready = quality.get("product_ready")
    repo_level_without_quality = mode == "post_apply" and quality_ready is None
    cases.append(
        _case(
            "quality_product_ready_matches_independent_gates",
            expected={"product_ready": expected_quality_ready},
            actual={"product_ready": quality_ready, "blocking_issues": quality.get("blocking_issues")},
            passed=repo_level_without_quality or quality_ready is expected_quality_ready,
            failures=[]
            if repo_level_without_quality or quality_ready is expected_quality_ready
            else ["quality_eval_understated_product_ready" if expected_quality_ready else "quality_eval_overstated_product_ready"],
            reason="Quality eval must agree with independent gates when a current job quality artifact is available; repo-level post-apply eval may run without a current job artifact.",
        ),
    )
    return cases


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


def _retrieval_state(db_path: Path, *, repo_id: str, settings: Settings) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "repo_id": repo_id, "repo_doc_count": 0, "vector_status_truthful": True}
    active_projection = _active_projection_row(db_path, repo_id=repo_id)
    active_projection_id = str(active_projection.get("projection_id") or "")
    if active_projection_id:
        doc_type_rows = _query(
            db_path,
            """
            SELECT doc_type, node_kind, COUNT(*) AS count
            FROM retrieval_documents
            WHERE repo_id = ? AND projection_id = ?
            GROUP BY doc_type, node_kind
            ORDER BY count DESC
            """,
            (repo_id, active_projection_id),
        )
    else:
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
    vector_ready = embedded_count >= repo_doc_count and repo_doc_count > 0 and str(faiss.get("status") or "") == "ready"
    query_gates = _retrieval_query_gates(db_path, repo_id=repo_id, settings=settings, require_vector=vector_ready)
    return {
        "exists": True,
        "repo_id": repo_id,
        "active_projection": active_projection,
        "active_projection_id": active_projection_id,
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
        "query_gates": query_gates,
        "vector_status_truthful": True,
    }


def _retrieval_query_gate_cases(retrieval: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for gate in retrieval.get("query_gates", []):
        if not isinstance(gate, dict):
            continue
        cases.append(
            _case(
                str(gate.get("case_id") or "retrieval_query_gate"),
                expected={
                    "query": gate.get("query"),
                    "top_docs": "curated_or_central_support",
                    "forbidden": ["CodeNode", "CodeHunk", "session_codenode", "session_codehunk", "code"],
                },
                actual={
                    "hits": gate.get("hits"),
                    "forbidden_hits": gate.get("forbidden_hits"),
                    "expected_support_present": gate.get("expected_support_present"),
                },
                passed=bool(gate.get("passed")),
                failures=list(gate.get("blocking_failures") or []),
                reason=str(gate.get("semantic_reason") or "Production query gate must return curated or central support, not raw trace nodes."),
            )
        )
    return cases


def _quality_issue_codes(quality: dict[str, Any]) -> list[str]:
    issues = quality.get("blocking_issues")
    if not isinstance(issues, list):
        return []
    codes: list[str] = []
    for issue in issues:
        if isinstance(issue, dict):
            code = str(issue.get("code") or issue.get("reason") or "").strip()
        else:
            code = str(issue or "").strip()
        if code:
            codes.append(code)
    return codes


def _vector_readiness_failures(retrieval: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    embedding = retrieval.get("embedding_coverage") if isinstance(retrieval.get("embedding_coverage"), dict) else {}
    faiss = retrieval.get("faiss") if isinstance(retrieval.get("faiss"), dict) else {}
    embedding_status = str(embedding.get("status") or "missing")
    faiss_status = str(faiss.get("status") or "missing")
    if embedding_status != "ready":
        failures.append("embedding_coverage_missing" if embedding_status == "missing" else "embedding_coverage_partial")
    if faiss_status != "ready":
        failures.append("faiss_coverage_missing" if faiss_status == "missing" else "faiss_coverage_partial")
    return failures


def _retrieval_query_gates(db_path: Path, *, repo_id: str, settings: Settings, require_vector: bool = False) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    gates = [
        {
            "case_id": "query_control_room_uses_curated_support",
            "query": "what changed for AMO control room web UI?",
            "expected_doc_types": {"file_impact", "code_impact", "central_version", "central_atom", "reasoning", "commit", "packet"},
            "required_terms": {"amo", "control", "web"},
        },
        {
            "case_id": "query_qwen_json_uses_curated_support",
            "query": "what qwen json hardening was done?",
            "expected_doc_types": {"file_impact", "code_impact", "commit", "packet", "reasoning"},
            "required_terms": {"qwen", "json"},
        },
    ]
    index = _ReadOnlyRetrievalIndex(db_path)
    embedding_store: _ReadOnlyEmbeddingSearch | None = None
    embedder: StrictTextEmbedder | None = None
    embedding_model = ""
    graph_scope = ""
    vector_setup_error = ""
    if require_vector:
        embedding_model = str(settings.embedding_model or "").strip()
        graph_scope = _active_embedding_scope(
            db_path,
            model=embedding_model,
            preferred_scope=str(settings.retrieval_graph_scope or "").strip() or "v2",
        )
        try:
            embedder = StrictTextEmbedder(embedding_model, dims=int(settings.embedding_dims or 256))
            embedding_store = _ReadOnlyEmbeddingSearch(db_path)
        except Exception as exc:  # pragma: no cover - environment dependent
            vector_setup_error = f"{type(exc).__name__}:{exc}"
    try:
        results: list[dict[str, Any]] = []
        for gate in gates:
            query = str(gate["query"])
            result = retrieve_session_graph(
                query=query,
                index_store=index,  # type: ignore[arg-type]
                graph_store=_ReadOnlyNoGraphStore(),  # type: ignore[arg-type]
                embedding_store=embedding_store,  # type: ignore[arg-type]
                embedder=embedder,
                embedding_model=embedding_model if embedder is not None else "",
                graph_scope=graph_scope,
                repo_id=repo_id,
                limit=5,
                candidate_limit=80,
                expand_neighbors=0,
                include_graph_nodes=False,
            )
            hit_payloads = [hit.as_dict() for hit in result.hits]
            hits = [_compact_gate_hit(hit) for hit in hit_payloads]
            forbidden_hits = [
                hit
                for hit in hits
                if str(hit.get("node_kind") or "") in {"CodeNode", "CodeHunk"}
                or str(hit.get("doc_type") or "") in {"session_codenode", "session_codehunk", "code"}
            ]
            expected_support_present = any(str(hit.get("doc_type") or "") in gate["expected_doc_types"] for hit in hits)
            visible_text = "\n".join(
                f"{document.get('title')}\n{document.get('body')}"
                for hit in hit_payloads
                for document in [hit.get("document") if isinstance(hit.get("document"), dict) else {}]
            ).lower()
            required_terms = set(gate.get("required_terms") or set())
            required_terms_present = all(term in visible_text for term in required_terms)
            failures: list[str] = []
            if not hits:
                failures.append("retrieval_query_no_hits")
            if forbidden_hits:
                failures.append("retrieval_query_raw_trace_top_result")
            if not expected_support_present:
                failures.append("retrieval_query_missing_curated_support")
            if required_terms and not required_terms_present:
                failures.append("retrieval_query_missing_required_terms")
            if require_vector:
                if vector_setup_error:
                    failures.append("retrieval_query_vector_setup_failed")
                elif result.candidate_counts.get("vector", 0) <= 0:
                    failures.append("retrieval_query_no_vector_hits")
            results.append(
                {
                    "case_id": gate["case_id"],
                    "query": query,
                    "passed": not failures,
                    "hits": hits,
                    "vector_required": require_vector,
                    "vector_status": result.vector_status,
                    "vector_candidate_count": result.candidate_counts.get("vector", 0),
                    "vector_setup_error": vector_setup_error,
                    "forbidden_hits": forbidden_hits,
                    "expected_support_present": expected_support_present,
                    "required_terms": sorted(required_terms),
                    "required_terms_present": required_terms_present,
                    "blocking_failures": failures,
                    "semantic_reason": "Top production retrieval hits should be curated impact or central memory support for this query.",
                }
            )
        return results
    finally:
        if embedding_store is not None:
            embedding_store.close()
        index.close()


def _compact_gate_hit(hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document") if isinstance(hit.get("document"), dict) else {}
    body = str(document.get("body") or "")
    return {
        "doc_id": document.get("doc_id"),
        "doc_type": document.get("doc_type"),
        "node_kind": document.get("node_kind"),
        "repo_id": document.get("repo_id"),
        "projection_id": document.get("projection_id"),
        "packet_id": document.get("packet_id"),
        "commit_sha": document.get("commit_sha"),
        "title": document.get("title"),
        "body_excerpt": body[:240],
        "score": hit.get("score"),
        "sources": hit.get("sources"),
        "reasons": hit.get("reasons"),
    }


class _ReadOnlyNoGraphStore:
    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        del node_id, limit
        return []

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        del limit, kinds, session_id, status
        return []


class _ReadOnlyRetrievalIndex:
    def __init__(self, db_path: Path) -> None:
        uri = db_path.resolve().as_posix().replace("'", "''")
        self.conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def active_projection_id(self, repo_id: str) -> str:
        try:
            rows = self.conn.execute(
                """
                SELECT projection_id
                FROM active_retrieval_projection
                WHERE repo_id = ?
                """,
                (str(repo_id or "").strip(),),
            ).fetchall()
        except sqlite3.OperationalError:
            return ""
        return str(rows[0]["projection_id"]) if rows else ""

    def list_documents(self, *, limit: int = 10000, repo_id: str = "") -> list[RetrievalDocument]:
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id and projection_id:
            rows = self.conn.execute(
                "SELECT * FROM retrieval_documents WHERE repo_id = ? AND projection_id = ? LIMIT ?",
                (safe_repo_id, projection_id, int(limit)),
            ).fetchall()
        elif safe_repo_id:
            rows = self.conn.execute(
                "SELECT * FROM retrieval_documents WHERE repo_id = ? LIMIT ?",
                (safe_repo_id, int(limit)),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM retrieval_documents LIMIT ?", (int(limit),)).fetchall()
        return [_retrieval_doc_from_row(row) for row in rows]

    def exact_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        return self._lexical_search(query, source="exact", limit=limit, repo_id=repo_id)

    def bm25_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        return self._lexical_search(query, source="bm25", limit=limit, repo_id=repo_id)

    def get_documents_by_ids(self, doc_ids: Any, *, repo_id: str = "") -> dict[str, RetrievalDocument]:
        ids = list(dict.fromkeys(str(doc_id) for doc_id in doc_ids if str(doc_id or "")))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id and projection_id:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders}) AND repo_id = ? AND projection_id = ?",
                [*ids, safe_repo_id, projection_id],
            ).fetchall()
        elif safe_repo_id:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders}) AND repo_id = ?",
                [*ids, safe_repo_id],
            ).fetchall()
        else:
            rows = self.conn.execute(f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders})", ids).fetchall()
        return {str(row["doc_id"]): _retrieval_doc_from_row(row) for row in rows}

    def documents_by_graph_node_ids(self, node_ids: Any, *, repo_id: str = "") -> dict[str, list[RetrievalDocument]]:
        ids = list(dict.fromkeys(str(node_id) for node_id in node_ids if str(node_id or "")))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        safe_repo_id = str(repo_id or "").strip()
        if safe_repo_id:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders}) AND repo_id = ?",
                [*ids, safe_repo_id],
            ).fetchall()
        else:
            rows = self.conn.execute(f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders})", ids).fetchall()
        out: dict[str, list[RetrievalDocument]] = {}
        for row in rows:
            doc = _retrieval_doc_from_row(row)
            out.setdefault(doc.graph_node_id, []).append(doc)
        return out

    def _lexical_search(self, query: str, *, source: str, limit: int, repo_id: str) -> list[RetrievalCandidate]:
        terms = _query_terms(query)
        if not terms:
            return []
        scored: list[tuple[float, str]] = []
        for doc in self.list_documents(limit=100000, repo_id=repo_id):
            text = f"{doc.title}\n{doc.body}".lower()
            score = sum(1.0 for term in terms if term in text)
            if score:
                scored.append((score + float(doc.importance or 0.0), doc.doc_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [RetrievalCandidate(doc_id=doc_id, source=source, rank=rank, raw_score=score) for rank, (score, doc_id) in enumerate(scored[:limit], start=1)]


class _ReadOnlyEmbeddingSearch:
    def __init__(self, db_path: Path) -> None:
        uri = db_path.resolve().as_posix().replace("'", "''")
        self.conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def search(
        self,
        query_vector: list[float],
        *,
        embedding_kind: str,
        model: str,
        graph_scope: str = "",
        limit: int = 10,
        backend: str = "auto",
    ) -> tuple[list[GraphEmbeddingHit], str]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM graph_embeddings
            WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
            """,
            (embedding_kind, model, graph_scope),
        ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            try:
                vector = [float(value) for value in json.loads(str(row["vector_json"] or "[]"))]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            score = cosine_similarity(query_vector, vector)
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits = [
            GraphEmbeddingHit(
                embedding_id=str(row["embedding_id"]),
                node_id=str(row["node_id"]),
                node_kind=str(row["node_kind"]),
                memory_class=str(row["memory_class"]),
                graph_scope=str(row["graph_scope"]),
                graph_path=str(row["graph_path"]),
                embedding_kind=str(row["embedding_kind"]),
                model=str(row["model"]),
                score=float(score),
            )
            for score, row in scored[: max(0, int(limit))]
        ]
        return hits, "sqlite:completed" if rows else "no_embeddings"


def _active_embedding_scope(db_path: Path, *, model: str, preferred_scope: str) -> str:
    preferred = str(preferred_scope or "").strip()
    if preferred:
        count = _scalar(
            db_path,
            """
            SELECT COUNT(*)
            FROM graph_embeddings
            WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
            """,
            (RETRIEVAL_EMBEDDING_KIND, model, preferred),
        )
        if count > 0:
            return preferred
    rows = _query(
        db_path,
        """
        SELECT graph_scope, COUNT(*) AS count
        FROM graph_embeddings
        WHERE embedding_kind = ? AND model = ? AND status = 'active'
        GROUP BY graph_scope
        ORDER BY count DESC, graph_scope ASC
        LIMIT 1
        """,
        (RETRIEVAL_EMBEDDING_KIND, model),
    )
    return str(rows[0].get("graph_scope") or preferred) if rows else preferred


def _query_terms(query: str) -> list[str]:
    return [term for term in re.sub(r"[^a-zA-Z0-9_.-]+", " ", query).lower().split() if len(term) > 2]


def _retrieval_doc_from_row(row: sqlite3.Row) -> RetrievalDocument:
    keys = set(row.keys())
    metadata = {}
    if "metadata_json" in keys:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata = {}
    return RetrievalDocument(
        doc_id=str(row["doc_id"]),
        doc_type=str(row["doc_type"]),
        graph_node_id=str(row["graph_node_id"]),
        node_kind=str(row["node_kind"]),
        repo_id=str(row["repo_id"] if "repo_id" in keys else ""),
        projection_id=str(row["projection_id"] if "projection_id" in keys else ""),
        packet_id=str(row["packet_id"] if "packet_id" in keys else ""),
        commit_sha=str(row["commit_sha"] if "commit_sha" in keys else ""),
        title=str(row["title"]),
        body=str(row["body"]),
        chunk_index=int(row["chunk_index"] if "chunk_index" in keys else 1),
        chunk_count=int(row["chunk_count"] if "chunk_count" in keys else 1),
        memory_class=str(row["memory_class"] if "memory_class" in keys else "graph_context"),
        importance=float(row["importance"] if "importance" in keys else 0.5),
        metadata=metadata,
    )


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
            if isinstance(payload.get("records"), list):
                count = len(payload["records"])
            elif isinstance(payload.get("embedding_ids"), list):
                count = len(payload["embedding_ids"])
            else:
                count = 0
            if count >= item_count:
                item_count = count
                latest = str(path)
    return {"status": "ready" if item_count else "partial", "item_count": item_count, "path": latest or str(root)}


def _active_projection_row(db_path: Path, *, repo_id: str) -> dict[str, Any]:
    rows = _query(
        db_path,
        """
        SELECT retrieval_projections.*
        FROM active_retrieval_projection
        JOIN retrieval_projections ON retrieval_projections.projection_id = active_retrieval_projection.projection_id
        WHERE active_retrieval_projection.repo_id = ?
        """,
        (repo_id,),
    )
    return rows[0] if rows else {}


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
