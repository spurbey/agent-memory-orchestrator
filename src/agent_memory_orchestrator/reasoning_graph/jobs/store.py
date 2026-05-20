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


class V2SessionJobStore:
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
        source_evidence_day: str = "",
        source_evidence_days: list[str] | tuple[str, ...] = (),
        pipeline_version: str = PIPELINE_VERSION,
        graph_schema_version: str = GRAPH_SCHEMA_VERSION,
    ) -> EnqueueResult:
        safe_session = str(session_id or "").strip()
        if not safe_session:
            raise ValueError("session_id is required")
        now = utc_now()
        job_id = stable_job_id(safe_session, pipeline_version)
        artifact_dir = str(default_artifact_dir(self.settings.home, pipeline_version, safe_session, job_id))
        days = _dedupe([source_evidence_day, *[str(item) for item in source_evidence_days if str(item)]])
        existing = self.get_job_by_session(session_id=safe_session, pipeline_version=pipeline_version)
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO v2_session_jobs(
                  job_id, session_id, pipeline_version, graph_schema_version, status,
                  current_stage, last_successful_stage, artifact_dir, source_app, repo_path,
                  boundary_event_id, source_evidence_day, source_evidence_days_json,
                  created_at, updated_at
                )
                VALUES(?, ?, ?, ?, 'pending', ?, '', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    safe_session,
                    pipeline_version,
                    graph_schema_version,
                    V2_STAGES[0],
                    artifact_dir,
                    source_app,
                    repo_path,
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
            return EnqueueResult(existing, created=False, updated=False, reason="already_enqueued")

        self.conn.execute(
            """
            UPDATE v2_session_jobs
            SET status='pending',
                current_stage=?,
                last_successful_stage='',
                source_app=?,
                repo_path=?,
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
                repo_path or str(existing.get("repo_path") or ""),
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

    def list_jobs(self, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM v2_session_jobs ORDER BY updated_at DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [_row(row) for row in rows]

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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    for key in ("source_evidence_days_json", "error_json", "diagnostics_json", "metadata_json", "value_json"):
        if key in out:
            try:
                out[key.removesuffix("_json")] = json.loads(out[key])
            except json.JSONDecodeError:
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


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
