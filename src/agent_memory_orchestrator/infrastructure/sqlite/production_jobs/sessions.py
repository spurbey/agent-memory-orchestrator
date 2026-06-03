from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ....domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ....domain.pipeline.constants import PIPELINE_VERSION
from ....domain.pipeline.constants import PRODUCTION_STAGES
from ....domain.pipeline.constants import RESET_MARKER_KEY
from ....domain.versioning.repo_identity import resolve_repo_identity
from .base import EnqueueResult
from .base import _dedupe
from .base import _next_stage
from .base import _row
from .base import default_artifact_dir
from .base import stable_job_id
from .base import utc_now

LEGACY_RESET_MARKER_KEY = "production_v2_reset"
LEGACY_RESET_PIPELINE_VERSION = "v2-reset-2026-05"
LEGACY_RESET_GRAPH_SCHEMA_VERSION = "v2"


class SessionJobStoreMixin:
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
        source_first_event_id: str = "",
        source_latest_event_id: str = "",
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
                  boundary_event_id, source_first_event_id, source_latest_event_id,
                  source_evidence_day, source_evidence_days_json,
                  created_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    safe_session,
                    pipeline_version,
                    graph_schema_version,
                    "pending",
                    PRODUCTION_STAGES[0],
                    "",
                    artifact_dir,
                    source_app,
                    safe_repo_path,
                    safe_repo_id,
                    boundary_event_id,
                    source_first_event_id,
                    source_latest_event_id,
                    source_evidence_day,
                    json.dumps(days),
                    now,
                    now,
                ),
            )
            self.conn.commit()
            self.log_event(
                job_id=job_id,
                event_type="enqueued",
                stage="",
                message="closed session enqueued",
                metadata={
                    "boundary_event_id": boundary_event_id,
                    "source_first_event_id": source_first_event_id,
                    "source_latest_event_id": source_latest_event_id,
                },
            )
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
                source_first_event_id=?,
                source_latest_event_id=?,
                source_evidence_day=?,
                source_evidence_days_json=?,
                lock_owner='',
                lock_expires_at='',
                error_json='{}',
                updated_at=?
            WHERE job_id=?
            """,
            (
                PRODUCTION_STAGES[0],
                source_app or str(existing.get("source_app") or ""),
                safe_repo_path or str(existing.get("repo_path") or ""),
                safe_repo_id or str(existing.get("repo_id") or ""),
                boundary_event_id,
                source_first_event_id,
                source_latest_event_id,
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
            message="closed session boundary changed; invalidated production stages",
            metadata={
                "boundary_event_id": boundary_event_id,
                "source_first_event_id": source_first_event_id,
                "source_latest_event_id": source_latest_event_id,
            },
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
        if row is None and key == RESET_MARKER_KEY:
            return self._legacy_reset_marker()
        if row is None:
            return None
        try:
            payload = json.loads(row["value_json"])
        except json.JSONDecodeError:
            payload = {}
        return {"marker_key": row["marker_key"], **payload, "created_at": row["created_at"], "updated_at": row["updated_at"]}

    def _legacy_reset_marker(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM v2_production_markers WHERE marker_key = ?", (LEGACY_RESET_MARKER_KEY,)).fetchone()
        payload: dict[str, Any] = {}
        created_at = ""
        updated_at = ""
        if row is not None:
            try:
                payload = json.loads(row["value_json"])
            except json.JSONDecodeError:
                payload = {}
            created_at = str(row["created_at"] or "")
            updated_at = str(row["updated_at"] or "")
        if not payload:
            legacy_file = self.settings.home / ".state" / f"{LEGACY_RESET_MARKER_KEY}.json"
            if not legacy_file.exists():
                return None
            try:
                payload = json.loads(legacy_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return None
        if (
            payload.get("pipeline_version") != LEGACY_RESET_PIPELINE_VERSION
            or payload.get("graph_schema_version") != LEGACY_RESET_GRAPH_SCHEMA_VERSION
        ):
            return None
        cleaned = payload.get("cleaned") if isinstance(payload.get("cleaned"), dict) else {}
        if cleaned.get("graph") is not True or cleaned.get("retrieval") is not True:
            return None
        return {
            "marker_key": RESET_MARKER_KEY,
            "pipeline_version": PIPELINE_VERSION,
            "graph_schema_version": GRAPH_SCHEMA_VERSION,
            "backup_path": str(payload.get("backup_path") or ""),
            "cleaned": {
                "graph": bool(cleaned.get("graph")),
                "retrieval": bool(cleaned.get("retrieval")),
                "faiss": bool(cleaned.get("faiss")),
            },
            "validated": payload.get("validated") if isinstance(payload.get("validated"), dict) else {},
            "validation": payload.get("validation") if isinstance(payload.get("validation"), dict) else {},
            "legacy_marker_key": LEGACY_RESET_MARKER_KEY,
            "legacy_pipeline_version": LEGACY_RESET_PIPELINE_VERSION,
            "legacy_graph_schema_version": LEGACY_RESET_GRAPH_SCHEMA_VERSION,
            "adopted_legacy_production_marker": True,
            "created_at": created_at,
            "updated_at": updated_at,
        }

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


__all__ = ["SessionJobStoreMixin"]
