from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ....domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ....domain.pipeline.constants import PIPELINE_VERSION
from ....domain.versioning.graph_views import graph_view_id
from .base import _row
from .base import utc_now


class CentralMergeStoreMixin:
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


__all__ = ["CentralMergeStoreMixin"]
