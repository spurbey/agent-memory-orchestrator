from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ...core.db import connect
from ...core.db import init_schema
from ...domain.versioning.repo_identity import resolve_repo_identity
from .constants import GRAPH_SCHEMA_VERSION
from .constants import PIPELINE_VERSION
from .constants import RESET_MARKER_KEY
from .constants import V2_STAGES


JOB_STATUSES = frozenset({"pending", "running", "pending_model", "failed", "complete"})
STAGE_STATUSES = frozenset({"pending", "running", "skipped", "pending_model", "failed", "complete"})


@dataclass(slots=True, frozen=True)
class EnqueueResult:
    job: dict[str, Any]
    created: bool
    updated: bool
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"job": self.job, "created": self.created, "updated": self.updated, "reason": self.reason}


class ProductionSessionJobStore:
    def __init__(self, settings: Settings, *, db_path: Path | None = None) -> None:
        self.settings = settings
        self.db_path = db_path or settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = connect(self.db_path)
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    def enqueue_session(
        self,
        *,
        session_id: str,
        boundary_event_id: str,
        source_app: str = "",
        repo_path: str = "",
        repo_id: str = "",
        source_evidence_day: str = "",
        source_evidence_days: list[str] | tuple[str, ...] = (),
        pipeline_version: str = PIPELINE_VERSION,
        graph_schema_version: str = GRAPH_SCHEMA_VERSION,
    ) -> EnqueueResult:
        safe_session = str(session_id or "").strip()
        if not safe_session:
            raise ValueError("session_id is required")
        now = utc_now()
        safe_repo_path = str(repo_path or "")
        safe_repo_id = str(repo_id or "").strip()
        if not safe_repo_id and safe_repo_path:
            safe_repo_id = resolve_repo_identity(safe_repo_path).repo_id
        job_id = stable_job_id(safe_session, pipeline_version)
        artifact_dir = str(default_artifact_dir(self.settings.home, pipeline_version, safe_session, job_id))
        days = _dedupe([source_evidence_day, *[str(item) for item in source_evidence_days if str(item)]])
        existing = self.get_job_by_session(session_id=safe_session, pipeline_version=pipeline_version)
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO v2_session_jobs(
                  job_id, session_id, pipeline_version, graph_schema_version, status,
                  current_stage, last_successful_stage, artifact_dir, source_app, repo_path, repo_id,
                  boundary_event_id, source_evidence_day, source_evidence_days_json,
                  created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 'pending', ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    safe_session,
                    pipeline_version,
                    graph_schema_version,
                    V2_STAGES[0],
                    artifact_dir,
                    source_app,
                    safe_repo_path,
                    safe_repo_id,
                    boundary_event_id,
                    source_evidence_day,
                    json.dumps(days),
                    now,
                    now,
                ),
            )
            self.conn.commit()
            self.log_event(job_id=job_id, event_type="enqueued", stage="", message="closed session enqueued", metadata={"boundary_event_id": boundary_event_id})
            return EnqueueResult(self.get_job(job_id) or {}, created=True, updated=False, reason="created")

        if str(existing.get("boundary_event_id") or "") == str(boundary_event_id or ""):
            if safe_repo_id and not str(existing.get("repo_id") or ""):
                existing = self.update_job_repo_identity(
                    job_id=str(existing["job_id"]),
                    repo_path=safe_repo_path,
                    repo_id=safe_repo_id,
                    reason="enqueue_repo_resolved",
                )
            return EnqueueResult(existing, created=False, updated=False, reason="already_enqueued")

        self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET status='pending',
                current_stage=?,
                last_successful_stage='',
                source_app=?,
                repo_path=?,
                repo_id=?,
                boundary_event_id=?,
                source_evidence_day=?,
                source_evidence_days_json=?,
                lock_owner='',
                lock_expires_at='',
                error_json='{}',
                updated_at=?
            WHERE job_id=?
            """,
            (
                V2_STAGES[0],
                source_app or str(existing.get("source_app") or ""),
                safe_repo_path or str(existing.get("repo_path") or ""),
                safe_repo_id or str(existing.get("repo_id") or ""),
                boundary_event_id,
                source_evidence_day,
                json.dumps(days),
                now,
                existing["job_id"],
            ),
        )
        self.conn.execute("DELETE FROM v2_session_job_stages WHERE job_id = ?", (existing["job_id"],))
        self.conn.commit()
        self.log_event(
            job_id=str(existing["job_id"]),
            event_type="reenqueued",
            stage="",
            message="closed session boundary changed; invalidated V2 stages",
            metadata={"boundary_event_id": boundary_event_id},
        )
        return EnqueueResult(self.get_job(str(existing["job_id"])) or {}, created=False, updated=True, reason="boundary_changed")

    def acquire_next(self, *, owner: str, lease_seconds: int = 300) -> dict[str, Any] | None:
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        cursor = self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET lock_owner = ?,
                lock_expires_at = ?,
                status = 'running',
                attempt_count = attempt_count + CASE WHEN status = 'pending' THEN 1 ELSE 0 END,
                last_attempt_at = CASE WHEN status = 'pending' THEN ? ELSE last_attempt_at END,
                updated_at = ?
            WHERE job_id = (
              SELECT job_id
              FROM v2_session_jobs
              WHERE status = 'pending'
                 OR (status = 'running' AND lock_expires_at != '' AND lock_expires_at < ?)
              ORDER BY created_at ASC
              LIMIT 1
            )
            """,
            (owner, expires, now, now, now),
        )
        if cursor.rowcount <= 0:
            self.conn.commit()
            return None
        self.conn.commit()
        row = self.conn.execute(
            "SELECT * FROM v2_session_jobs WHERE lock_owner = ? AND lock_expires_at = ? ORDER BY updated_at DESC LIMIT 1",
            (owner, expires),
        ).fetchone()
        return _row(row) if row is not None else None

    def retry_job(self, job_id: str, *, forced_by: str = "manual") -> dict[str, Any]:
        now = utc_now()
        row = self.conn.execute("SELECT * FROM v2_session_jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown_job:{job_id}")
        if row["status"] not in {"failed", "pending_model"}:
            raise ValueError(f"job_not_retryable:{row['status']}")
        self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET status='pending',
                lock_owner='',
                lock_expires_at='',
                forced_at=?,
                forced_by=?,
                updated_at=?
            WHERE job_id=?
            """,
            (now, forced_by, now, job_id),
        )
        self.conn.commit()
        self.log_event(job_id=job_id, event_type="retry", stage="", message="job manually retried", metadata={"forced_by": forced_by})
        return self.get_job(job_id) or {}

    def start_stage(self, *, job_id: str, stage: str, input_artifact: str, input_hash: str, stage_config_hash: str) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO v2_session_job_stages(
              job_id, stage, status, input_artifact, input_hash, stage_config_hash, started_at
            )
            VALUES(?, ?, 'running', ?, ?, ?, ?)
            ON CONFLICT(job_id, stage) DO UPDATE SET
              status='running',
              input_artifact=excluded.input_artifact,
              input_hash=excluded.input_hash,
              stage_config_hash=excluded.stage_config_hash,
              started_at=excluded.started_at,
              finished_at='',
              diagnostics_json='{}'
            """,
            (job_id, stage, input_artifact, input_hash, stage_config_hash, now),
        )
        self.conn.execute(
            "UPDATE v2_session_jobs SET status='running', current_stage=?, updated_at=? WHERE job_id=?",
            (stage, now, job_id),
        )
        self.conn.commit()
        self.log_event(job_id=job_id, event_type="stage_started", stage=stage, message=f"stage started: {stage}", metadata={})

    def complete_stage(
        self,
        *,
        job_id: str,
        stage: str,
        output_artifact: str,
        output_hash: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        next_stage = _next_stage(stage)
        job_status = "complete" if next_stage == "" else "pending"
        self.conn.execute(
            """
            UPDATE v2_session_job_stages
            SET status='complete',
                output_artifact=?,
                output_hash=?,
                diagnostics_json=?,
                finished_at=?
            WHERE job_id=? AND stage=?
            """,
            (output_artifact, output_hash, json.dumps(diagnostics or {}, sort_keys=True), now, job_id, stage),
        )
        self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET status=?,
                current_stage=?,
                last_successful_stage=?,
                error_json='{}',
                updated_at=?
            WHERE job_id=?
            """,
            (job_status, next_stage, stage, now, job_id),
        )
        self.conn.commit()
        self.log_event(job_id=job_id, event_type="stage_completed", stage=stage, message=f"stage completed: {stage}", metadata=diagnostics or {})

    def set_pending_model(self, *, job_id: str, stage: str, reason: str, diagnostics: dict[str, Any] | None = None) -> None:
        self._stop_stage(job_id=job_id, stage=stage, status="pending_model", reason=reason, diagnostics=diagnostics)

    def fail_stage(self, *, job_id: str, stage: str, reason: str, diagnostics: dict[str, Any] | None = None) -> None:
        self._stop_stage(job_id=job_id, stage=stage, status="failed", reason=reason, diagnostics=diagnostics)

    def release_lock(self, *, job_id: str) -> None:
        self.conn.execute(
            "UPDATE v2_session_jobs SET lock_owner='', lock_expires_at='', updated_at=? WHERE job_id=?",
            (utc_now(), job_id),
        )
        self.conn.commit()

    def update_job_repo_path(
        self,
        *,
        job_id: str,
        repo_path: str,
        reason: str = "repo_resolved",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        return self.update_job_repo_identity(job_id=job_id, repo_path=repo_path, repo_id="", reason=reason, metadata=metadata)

    def update_job_repo_identity(
        self,
        *,
        job_id: str,
        repo_path: str,
        repo_id: str = "",
        reason: str = "repo_resolved",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now()
        safe_repo_path = str(repo_path or "").strip()
        safe_repo_id = str(repo_id or "").strip()
        self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET repo_path=?,
                repo_id=CASE WHEN ? != '' THEN ? ELSE repo_id END,
                updated_at=?
            WHERE job_id=?
            """,
            (safe_repo_path, safe_repo_id, safe_repo_id, now, job_id),
        )
        self.conn.commit()
        self.log_event(
            job_id=job_id,
            event_type="repo_resolved",
            stage="",
            message=reason,
            metadata={"repo_path": safe_repo_path, "repo_id": safe_repo_id, **(metadata or {})},
        )
        return self.get_job(job_id)

    def list_jobs(self, *, limit: int = 100, repo_id: str = "") -> list[dict[str, Any]]:
        safe_repo_id = str(repo_id or "").strip()
        if safe_repo_id:
            rows = self.conn.execute(
                "SELECT * FROM v2_session_jobs WHERE repo_id = ? ORDER BY updated_at DESC LIMIT ?",
                (safe_repo_id, max(1, int(limit))),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM v2_session_jobs ORDER BY updated_at DESC LIMIT ?",
                (max(1, int(limit)),),
            ).fetchall()
        return [_row(row) for row in rows]

    def list_repositories(self, *, limit: int = 200) -> list[dict[str, Any]]:
        repos: dict[str, dict[str, Any]] = {}

        def add(repo_id: str, repo_path: str, *, source: str, updated_at: str = "", job_count: int = 0, plan_count: int = 0) -> None:
            key = str(repo_id or "").strip()
            if not key:
                return
            row = repos.setdefault(
                key,
                {
                    "repo_id": key,
                    "repo_path": "",
                    "sources": set(),
                    "job_count": 0,
                    "plan_count": 0,
                    "updated_at": "",
                },
            )
            if repo_path and (not row["repo_path"] or len(repo_path) < len(str(row["repo_path"]))):
                row["repo_path"] = repo_path
            row["sources"].add(source)
            row["job_count"] += int(job_count)
            row["plan_count"] += int(plan_count)
            if updated_at and updated_at > str(row["updated_at"]):
                row["updated_at"] = updated_at

        for row in self.conn.execute(
            """
            SELECT repo_id, repo_path, count(*) AS job_count, max(updated_at) AS updated_at
            FROM v2_session_jobs
            WHERE repo_id != ''
            GROUP BY repo_id, repo_path
            """
        ).fetchall():
            add(
                str(row["repo_id"] or ""),
                str(row["repo_path"] or ""),
                source="v2_session_jobs",
                updated_at=str(row["updated_at"] or ""),
                job_count=int(row["job_count"] or 0),
            )
        for row in self.conn.execute(
            """
            SELECT repo_id, repo_path, count(*) AS plan_count, max(updated_at) AS updated_at
            FROM v2_central_merge_plans
            WHERE repo_id != ''
            GROUP BY repo_id, repo_path
            """
        ).fetchall():
            add(
                str(row["repo_id"] or ""),
                str(row["repo_path"] or ""),
                source="v2_central_merge_plans",
                updated_at=str(row["updated_at"] or ""),
                plan_count=int(row["plan_count"] or 0),
            )
        out: list[dict[str, Any]] = []
        for row in repos.values():
            out.append({**row, "sources": sorted(row["sources"])})
        out.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return out[: max(1, int(limit))]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM v2_session_jobs WHERE job_id = ?", (job_id,)).fetchone()
        return _row(row) if row is not None else None

    def get_job_by_session(self, *, session_id: str, pipeline_version: str = PIPELINE_VERSION) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM v2_session_jobs WHERE session_id = ? AND pipeline_version = ?",
            (session_id, pipeline_version),
        ).fetchone()
        return _row(row) if row is not None else None

    def list_stages(self, job_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM v2_session_job_stages WHERE job_id = ? ORDER BY rowid ASC",
            (job_id,),
        ).fetchall()
        return [_row(row) for row in rows]

    def stage_row(self, *, job_id: str, stage: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM v2_session_job_stages WHERE job_id = ? AND stage = ?",
            (job_id, stage),
        ).fetchone()
        return _row(row) if row is not None else None

    def list_events(self, job_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM v2_session_job_events WHERE job_id = ? ORDER BY created_at DESC LIMIT ?",
            (job_id, max(1, int(limit))),
        ).fetchall()
        return [_row(row) for row in rows]

    def log_event(
        self,
        *,
        job_id: str,
        event_type: str,
        stage: str = "",
        message: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO v2_session_job_events(event_id, job_id, event_type, stage, message, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"v2evt:{uuid.uuid4().hex}",
                job_id,
                event_type,
                stage,
                message,
                json.dumps(metadata or {}, sort_keys=True),
                utc_now(),
            ),
        )
        self.conn.commit()

    def marker(self, key: str = RESET_MARKER_KEY) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM v2_production_markers WHERE marker_key = ?", (key,)).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(row["value_json"])
        except json.JSONDecodeError:
            payload = {}
        return {"marker_key": row["marker_key"], **payload, "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def upsert_marker(self, key: str, value: dict[str, Any]) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO v2_production_markers(marker_key, value_json, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(marker_key) DO UPDATE SET
              value_json=excluded.value_json,
              updated_at=excluded.updated_at
            """,
            (key, json.dumps(value, sort_keys=True), now, now),
        )
        self.conn.commit()

    def upsert_central_merge_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        plan_id = str(plan.get("plan_id") or "")
        if not plan_id:
            raise ValueError("plan_id is required")
        graph_commit = plan.get("graph_commit_preview") if isinstance(plan.get("graph_commit_preview"), dict) else {}
        self.conn.execute(
            """
            INSERT INTO v2_central_merge_plans(
              plan_id, job_id, session_id, pipeline_version, graph_schema_version,
              plan_version, status, mode, repo_id, repo_path, parent_graph_commit_id,
              input_graph_hash, plan_hash, plan_json, metrics_json, diagnostics_json,
              created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(plan_id) DO UPDATE SET
              status=excluded.status,
              mode=excluded.mode,
              repo_id=excluded.repo_id,
              repo_path=excluded.repo_path,
              parent_graph_commit_id=excluded.parent_graph_commit_id,
              input_graph_hash=excluded.input_graph_hash,
              plan_hash=excluded.plan_hash,
              plan_json=excluded.plan_json,
              metrics_json=excluded.metrics_json,
              diagnostics_json=excluded.diagnostics_json,
              updated_at=excluded.updated_at
            """,
            (
                plan_id,
                str(plan.get("job_id") or ""),
                str(plan.get("session_id") or ""),
                str(plan.get("pipeline_version") or PIPELINE_VERSION),
                str(plan.get("graph_schema_version") or GRAPH_SCHEMA_VERSION),
                str(plan.get("plan_version") or ""),
                str(plan.get("status") or "planned"),
                str(plan.get("mode") or "dry_run"),
                str(plan.get("repo_id") or ""),
                str(plan.get("repo_path") or ""),
                str(plan.get("parent_graph_commit_id") or ""),
                str(plan.get("input_graph_hash") or ""),
                str(plan.get("plan_hash") or ""),
                json.dumps(plan, sort_keys=True),
                json.dumps(plan.get("metrics") or {}, sort_keys=True),
                json.dumps(plan.get("diagnostics") or {}, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.execute("DELETE FROM v2_central_review_candidates WHERE plan_id = ?", (plan_id,))
        for candidate in plan.get("review_candidates", []) if isinstance(plan.get("review_candidates"), list) else []:
            if not isinstance(candidate, dict):
                continue
            self.conn.execute(
                """
                INSERT INTO v2_central_review_candidates(
                  candidate_id, plan_id, job_id, source_node_id, target_node_id,
                  proposed_relation, score_json, reason, status, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(candidate.get("candidate_id") or f"v2review:{uuid.uuid4().hex}"),
                    plan_id,
                    str(candidate.get("job_id") or plan.get("job_id") or ""),
                    str(candidate.get("source_node_id") or ""),
                    str(candidate.get("target_node_id") or ""),
                    str(candidate.get("proposed_relation") or ""),
                    json.dumps(candidate.get("score") or {}, sort_keys=True),
                    str(candidate.get("reason") or ""),
                    str(candidate.get("status") or "open"),
                    now,
                    now,
                ),
            )
        self.conn.execute("DELETE FROM v2_central_decision_frames WHERE plan_id = ?", (plan_id,))
        diagnostics = plan.get("diagnostics") if isinstance(plan.get("diagnostics"), dict) else {}
        frames = diagnostics.get("decision_frames") if isinstance(diagnostics.get("decision_frames"), list) else []
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            frame_id = str(frame.get("frame_id") or "")
            if not frame_id:
                continue
            self.conn.execute(
                """
                INSERT INTO v2_central_decision_frames(
                  frame_id, plan_id, job_id, session_id, repo_id, source_node_id,
                  frame_kind, source_scope, subject, summary, statement, status,
                  frame_json, created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(frame_id) DO UPDATE SET
                  plan_id=excluded.plan_id,
                  job_id=excluded.job_id,
                  session_id=excluded.session_id,
                  repo_id=excluded.repo_id,
                  source_node_id=excluded.source_node_id,
                  frame_kind=excluded.frame_kind,
                  source_scope=excluded.source_scope,
                  subject=excluded.subject,
                  summary=excluded.summary,
                  statement=excluded.statement,
                  status=excluded.status,
                  frame_json=excluded.frame_json,
                  updated_at=excluded.updated_at
                """,
                (
                    frame_id,
                    plan_id,
                    str(plan.get("job_id") or ""),
                    str(plan.get("session_id") or ""),
                    str(frame.get("repo_id") or plan.get("repo_id") or ""),
                    str(frame.get("source_node_id") or ""),
                    str(frame.get("frame_kind") or ""),
                    str(frame.get("source_scope") or "session"),
                    str(frame.get("subject") or ""),
                    str(frame.get("summary") or ""),
                    str(frame.get("statement") or ""),
                    "review",
                    json.dumps(frame, sort_keys=True),
                    now,
                    now,
                ),
            )
        if graph_commit.get("graph_commit_id"):
            self.conn.execute(
                """
                INSERT INTO v2_graph_commits(
                  graph_commit_id, plan_id, job_id, repo_id, branch, parent_graph_commit_id,
                  status, pipeline_version, graph_schema_version, algorithm_versions_json,
                  added_nodes_json, added_edges_json, status_updates_json, diagnostics_json,
                  created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 'main', ?, ?, ?, ?, ?, '[]', '[]', '[]', ?, ?, ?)
                ON CONFLICT(graph_commit_id) DO UPDATE SET
                  plan_id=excluded.plan_id,
                  job_id=excluded.job_id,
                  repo_id=excluded.repo_id,
                  parent_graph_commit_id=excluded.parent_graph_commit_id,
                  status=excluded.status,
                  algorithm_versions_json=excluded.algorithm_versions_json,
                  diagnostics_json=excluded.diagnostics_json,
                  updated_at=excluded.updated_at
                """,
                (
                    str(graph_commit["graph_commit_id"]),
                    plan_id,
                    str(plan.get("job_id") or ""),
                    str(plan.get("repo_id") or ""),
                    str(graph_commit.get("parent_graph_commit_id") or ""),
                    str(graph_commit.get("status") or "preview"),
                    str(plan.get("pipeline_version") or PIPELINE_VERSION),
                    str(plan.get("graph_schema_version") or GRAPH_SCHEMA_VERSION),
                    json.dumps({"central_merge_plan": plan.get("plan_version", "")}, sort_keys=True),
                    json.dumps({"preview_only": True}, sort_keys=True),
                    now,
                    now,
                ),
            )
        self.ensure_graph_view(repo_id=str(plan.get("repo_id") or ""), branch="main", mode="active")
        self.conn.commit()
        return self.get_central_merge_plan(plan_id) or {}

    def get_central_merge_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM v2_central_merge_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        return _row(row) if row is not None else None

    def get_central_merge_plan_for_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM v2_central_merge_plans WHERE job_id = ? ORDER BY updated_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return _row(row) if row is not None else None

    def get_graph_commit(self, graph_commit_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM v2_graph_commits WHERE graph_commit_id = ?", (graph_commit_id,)).fetchone()
        return _row(row) if row is not None else None

    def get_graph_commit_for_plan(self, plan_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM v2_graph_commits WHERE plan_id = ? ORDER BY updated_at DESC LIMIT 1",
            (plan_id,),
        ).fetchone()
        return _row(row) if row is not None else None

    def update_central_merge_plan_status(
        self,
        *,
        plan_id: str,
        status: str,
        mode: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        row = self.conn.execute("SELECT plan_json, diagnostics_json FROM v2_central_merge_plans WHERE plan_id = ?", (plan_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown_central_merge_plan:{plan_id}")
        try:
            plan_json = json.loads(row["plan_json"])
        except (TypeError, json.JSONDecodeError):
            plan_json = {}
        try:
            diagnostics_json = json.loads(row["diagnostics_json"])
        except (TypeError, json.JSONDecodeError):
            diagnostics_json = {}
        plan_json["status"] = status
        if mode is not None:
            plan_json["mode"] = mode
        merged_diagnostics = {**diagnostics_json, **(diagnostics or {})}
        plan_json["diagnostics"] = {**(plan_json.get("diagnostics") if isinstance(plan_json.get("diagnostics"), dict) else {}), **(diagnostics or {})}
        self.conn.execute(
            """
            UPDATE v2_central_merge_plans
            SET status=?,
                mode=COALESCE(?, mode),
                plan_json=?,
                diagnostics_json=?,
                updated_at=?
            WHERE plan_id=?
            """,
            (
                status,
                mode,
                json.dumps(plan_json, sort_keys=True),
                json.dumps(merged_diagnostics, sort_keys=True),
                now,
                plan_id,
            ),
        )
        self.conn.commit()
        return self.get_central_merge_plan(plan_id) or {}

    def list_review_candidates(self, *, plan_id: str, status: str = "") -> list[dict[str, Any]]:
        if status:
            rows = self.conn.execute(
                "SELECT * FROM v2_central_review_candidates WHERE plan_id = ? AND status = ? ORDER BY created_at ASC",
                (plan_id, status),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM v2_central_review_candidates WHERE plan_id = ? ORDER BY created_at ASC",
                (plan_id,),
            ).fetchall()
        return [_row(row) for row in rows]

    def list_decision_frames(self, *, repo_id: str, exclude_job_id: str = "", status: str = "") -> list[dict[str, Any]]:
        clauses = ["repo_id = ?"]
        params: list[Any] = [repo_id]
        if exclude_job_id:
            clauses.append("job_id != ?")
            params.append(exclude_job_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        rows = self.conn.execute(
            f"SELECT * FROM v2_central_decision_frames WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC",
            tuple(params),
        ).fetchall()
        return [_row(row) for row in rows]

    def ensure_graph_view(self, *, repo_id: str = "", branch: str = "main", mode: str = "active") -> dict[str, Any]:
        safe_repo_id = str(repo_id or "").strip()
        existing = self.graph_view(repo_id=safe_repo_id, branch=branch, mode=mode, status="active")
        if existing is not None:
            return existing
        now = utc_now()
        view_id = graph_view_id(repo_id=safe_repo_id, branch=branch, mode=mode)
        self.conn.execute(
            """
            INSERT OR IGNORE INTO v2_graph_views(view_id, repo_id, branch, mode, graph_commit_id, status, metadata_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, '', 'active', ?, ?, ?)
            """,
            (view_id, safe_repo_id, branch, mode, json.dumps({"empty_head": True, "repo_id": safe_repo_id}, sort_keys=True), now, now),
        )
        self.conn.commit()
        return self.graph_view(repo_id=safe_repo_id, branch=branch, mode=mode, status="active") or {}

    def graph_view(self, *, repo_id: str = "", branch: str = "main", mode: str = "active", status: str = "active") -> dict[str, Any] | None:
        safe_repo_id = str(repo_id or "").strip()
        row = self.conn.execute(
            """
            SELECT * FROM v2_graph_views
            WHERE repo_id = ? AND branch = ? AND mode = ? AND status = ?
            ORDER BY updated_at DESC LIMIT 1
            """,
            (safe_repo_id, branch, mode, status),
        ).fetchone()
        return _row(row) if row is not None else None

    def acquire_central_merge_lock(
        self,
        *,
        repo_id: str = "",
        branch: str,
        owner: str,
        expected_parent_graph_commit_id: str,
        lease_seconds: int = 300,
    ) -> bool:
        now = utc_now()
        expires = (datetime.now(timezone.utc) + timedelta(seconds=max(30, int(lease_seconds)))).isoformat()
        self.conn.execute(
            """
            INSERT OR IGNORE INTO v2_central_merge_locks(
              repo_id, branch, lock_owner, lock_expires_at, expected_parent_graph_commit_id, created_at, updated_at
            )
            VALUES(?, ?, '', '', '', ?, ?)
            """,
            (str(repo_id or "").strip(), branch, now, now),
        )
        cursor = self.conn.execute(
            """
            UPDATE v2_central_merge_locks
            SET lock_owner=?,
                lock_expires_at=?,
                expected_parent_graph_commit_id=?,
                updated_at=?
            WHERE repo_id=? AND branch=?
              AND (lock_owner='' OR lock_expires_at < ? OR lock_owner=?)
            """,
            (owner, expires, expected_parent_graph_commit_id, now, str(repo_id or "").strip(), branch, now, owner),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def release_central_merge_lock(self, *, branch: str, owner: str, repo_id: str = "") -> None:
        now = utc_now()
        self.conn.execute(
            """
            UPDATE v2_central_merge_locks
            SET lock_owner='',
                lock_expires_at='',
                updated_at=?
            WHERE repo_id=? AND branch=? AND lock_owner=?
            """,
            (now, str(repo_id or "").strip(), branch, owner),
        )
        self.conn.commit()

    def record_applied_graph_commit(
        self,
        *,
        graph_commit_id: str,
        plan_id: str,
        job_id: str,
        branch: str,
        parent_graph_commit_id: str,
        pipeline_version: str,
        graph_schema_version: str,
        algorithm_versions: dict[str, Any],
        added_nodes: list[str],
        added_edges: list[str],
        repo_id: str = "",
        status_updates: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO v2_graph_commits(
              graph_commit_id, plan_id, job_id, repo_id, branch, parent_graph_commit_id,
              status, pipeline_version, graph_schema_version, algorithm_versions_json,
              added_nodes_json, added_edges_json, status_updates_json, diagnostics_json,
              created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(graph_commit_id) DO UPDATE SET
              plan_id=excluded.plan_id,
              job_id=excluded.job_id,
              repo_id=excluded.repo_id,
              branch=excluded.branch,
              parent_graph_commit_id=excluded.parent_graph_commit_id,
              status='applied',
              pipeline_version=excluded.pipeline_version,
              graph_schema_version=excluded.graph_schema_version,
              algorithm_versions_json=excluded.algorithm_versions_json,
              added_nodes_json=excluded.added_nodes_json,
              added_edges_json=excluded.added_edges_json,
              status_updates_json=excluded.status_updates_json,
              diagnostics_json=excluded.diagnostics_json,
              updated_at=excluded.updated_at
            """,
            (
                graph_commit_id,
                plan_id,
                job_id,
                str(repo_id or "").strip(),
                branch,
                parent_graph_commit_id,
                pipeline_version,
                graph_schema_version,
                json.dumps(algorithm_versions, sort_keys=True),
                json.dumps(added_nodes, sort_keys=True),
                json.dumps(added_edges, sort_keys=True),
                json.dumps(status_updates or [], sort_keys=True),
                json.dumps(diagnostics or {}, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT * FROM v2_graph_commits WHERE graph_commit_id = ?", (graph_commit_id,)).fetchone()
        return _row(row) if row is not None else {}

    def update_graph_view_head(
        self,
        *,
        repo_id: str = "",
        branch: str = "main",
        mode: str = "active",
        graph_commit_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now()
        safe_repo_id = str(repo_id or "").strip()
        view_id = graph_view_id(repo_id=safe_repo_id, branch=branch, mode=mode)
        self.conn.execute(
            """
            INSERT INTO v2_graph_views(view_id, repo_id, branch, mode, graph_commit_id, status, metadata_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, 'active', ?, ?, ?)
            ON CONFLICT(repo_id, branch, mode, status) DO UPDATE SET
              graph_commit_id=excluded.graph_commit_id,
              metadata_json=excluded.metadata_json,
              updated_at=excluded.updated_at
            """,
            (
                view_id,
                safe_repo_id,
                branch,
                mode,
                graph_commit_id,
                json.dumps({"repo_id": safe_repo_id, **(metadata or {})}, sort_keys=True),
                now,
                now,
            ),
        )
        self.conn.commit()
        return self.graph_view(repo_id=safe_repo_id, branch=branch, mode=mode, status="active") or {}

    def record_semantic_eval_run(self, *, run_id: str, case_set: str, fixture_path: str, status: str, metrics: dict[str, Any], diagnostics: dict[str, Any] | None = None) -> None:
        now = utc_now()
        self.conn.execute(
            """
            INSERT INTO v2_semantic_eval_runs(run_id, case_set, fixture_path, status, metrics_json, diagnostics_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
              status=excluded.status,
              metrics_json=excluded.metrics_json,
              diagnostics_json=excluded.diagnostics_json,
              updated_at=excluded.updated_at
            """,
            (run_id, case_set, fixture_path, status, json.dumps(metrics, sort_keys=True), json.dumps(diagnostics or {}, sort_keys=True), now, now),
        )
        self.conn.commit()

    def _stop_stage(
        self,
        *,
        job_id: str,
        stage: str,
        status: str,
        reason: str,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"failed", "pending_model"}:
            raise ValueError(f"invalid stop status: {status}")
        now = utc_now()
        payload = {"reason": reason, **(diagnostics or {})}
        self.conn.execute(
            """
            UPDATE v2_session_job_stages
            SET status=?,
                diagnostics_json=?,
                finished_at=?
            WHERE job_id=? AND stage=?
            """,
            (status, json.dumps(payload, sort_keys=True), now, job_id, stage),
        )
        self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET status=?,
                current_stage=?,
                lock_owner='',
                lock_expires_at='',
                error_json=?,
                updated_at=?
            WHERE job_id=?
            """,
            (status, stage, json.dumps(payload, sort_keys=True), now, job_id),
        )
        self.conn.commit()
        self.log_event(job_id=job_id, event_type=status, stage=stage, message=reason, metadata=payload)


def stable_job_id(session_id: str, pipeline_version: str = PIPELINE_VERSION) -> str:
    digest = hashlib.sha256(f"{pipeline_version}|{session_id}".encode("utf-8")).hexdigest()[:32]
    return f"v2job:{digest}"


def default_artifact_dir(home: Path, pipeline_version: str, session_id: str, job_id: str) -> Path:
    return home / ".state" / "v2-jobs" / safe_part(pipeline_version) / f"{safe_part(session_id)[:80]}-{job_id.rsplit(':', 1)[-1][:8]}" / job_id.rsplit(":", 1)[-1]


def safe_part(value: str) -> str:
    out = []
    for ch in str(value):
        if ch.isalnum() or ch in {"-", "_", "."}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "value"


def graph_view_id(*, repo_id: str = "", branch: str = "main", mode: str = "active") -> str:
    safe_repo = safe_part(repo_id)[:72] if repo_id else ""
    if safe_repo:
        return f"v2view:{safe_repo}:{safe_part(branch)}:{safe_part(mode)}"
    return f"v2view:{safe_part(branch)}:{safe_part(mode)}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key, value in list(out.items()):
        if key.endswith("_json"):
            try:
                out[key.removesuffix("_json")] = json.loads(value)
            except (TypeError, json.JSONDecodeError):
                out[key.removesuffix("_json")] = {}
    return out


def _next_stage(stage: str) -> str:
    try:
        index = V2_STAGES.index(stage)
    except ValueError:
        return ""
    if index + 1 >= len(V2_STAGES):
        return ""
    return V2_STAGES[index + 1]


V2SessionJobStore = ProductionSessionJobStore


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
