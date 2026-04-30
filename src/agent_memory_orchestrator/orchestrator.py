from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from .config import Settings
from .db import connect, init_schema
from .models import AgentRole, OrchestrationState


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class OrchestratorService:
    def __init__(self, settings: Settings, conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings
        self.conn = conn or connect(settings.db_path)
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    def start(self, session_id: str, title: str | None = None) -> dict:
        ts = _utc_now()
        self.conn.execute(
            """
            INSERT INTO sessions(id, title, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=COALESCE(?, title),
              status=?,
              updated_at=?
            """,
            (session_id, title or session_id, OrchestrationState.DRAFT.value, ts, ts, title, OrchestrationState.DRAFT.value, ts),
        )
        self.conn.commit()
        return self.status(session_id)

    def _get_status(self, session_id: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"session does not exist: {session_id}")
        return row["status"]

    def _set_status(self, session_id: str, new_status: str) -> None:
        ts = _utc_now()
        self.conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (new_status, ts, session_id),
        )
        self.conn.commit()

    def _last_round(self, session_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """
            SELECT *
            FROM orchestration_rounds
            WHERE session_id = ?
            ORDER BY round_index DESC
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()

    def submit(
        self,
        session_id: str,
        agent: str,
        summary: str,
        confidence: float,
        artifact_uri: str = "",
        blocking_issues: list[str] | None = None,
    ) -> dict:
        if agent not in {AgentRole.CLAUDE.value, AgentRole.CODEX.value}:
            raise ValueError("agent must be one of: claude, codex")
        if not (0.0 <= confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")

        current_state = self._get_status(session_id)
        if current_state in {OrchestrationState.APPROVED.value, OrchestrationState.REJECTED.value}:
            raise ValueError("orchestration already finalized")
        if current_state == OrchestrationState.READY_FOR_USER.value:
            raise ValueError("waiting for user decision")

        last_round = self._last_round(session_id)
        round_index = 1 if last_round is None else int(last_round["round_index"]) + 1
        if round_index > self.settings.max_review_rounds:
            raise ValueError("max review rounds exceeded")

        if last_round is not None and last_round["agent"] == agent:
            raise ValueError("agent turns must alternate between claude and codex")

        issues = blocking_issues or []
        rid = _id("round")
        ts = _utc_now()

        self.conn.execute(
            """
            INSERT INTO orchestration_rounds(
              id, session_id, round_index, agent, summary, artifact_uri,
              blocking_issues_json, confidence, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (rid, session_id, round_index, agent, summary, artifact_uri, json.dumps(issues), confidence, ts),
        )

        if agent == AgentRole.CLAUDE.value:
            next_state = OrchestrationState.REVIEW.value
        else:
            if issues or confidence < self.settings.consensus_threshold:
                next_state = OrchestrationState.REVISE.value
            else:
                next_state = OrchestrationState.READY_FOR_USER.value

        self.conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (next_state, ts, session_id),
        )
        self.conn.commit()
        return self.status(session_id)

    def user_decision(self, session_id: str, decision: str, notes: str = "", decided_by: str = "user") -> dict:
        normalized = decision.strip().lower()
        if normalized not in {OrchestrationState.APPROVED.value, OrchestrationState.REJECTED.value}:
            raise ValueError("decision must be 'approved' or 'rejected'")

        current_state = self._get_status(session_id)
        if current_state != OrchestrationState.READY_FOR_USER.value:
            raise ValueError("user decision is allowed only when state=ready_for_user")

        did = _id("decision")
        ts = _utc_now()
        self.conn.execute(
            """
            INSERT INTO orchestration_decisions(
              id, session_id, decision, notes, decided_by, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (did, session_id, normalized, notes, decided_by, ts),
        )
        self.conn.execute(
            "UPDATE sessions SET status = ?, updated_at = ? WHERE id = ?",
            (normalized, ts, session_id),
        )
        self.conn.commit()
        return self.status(session_id)

    def status(self, session_id: str) -> dict:
        session_row = self.conn.execute(
            """
            SELECT id, title, status, created_at, updated_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_row is None:
            raise ValueError(f"session does not exist: {session_id}")

        rounds = self.conn.execute(
            """
            SELECT id, round_index, agent, summary, artifact_uri, blocking_issues_json, confidence, created_at
            FROM orchestration_rounds
            WHERE session_id = ?
            ORDER BY round_index ASC
            """,
            (session_id,),
        ).fetchall()

        decisions = self.conn.execute(
            """
            SELECT id, decision, notes, decided_by, created_at
            FROM orchestration_decisions
            WHERE session_id = ?
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()

        return {
            "session": dict(session_row),
            "rounds": [
                {
                    "id": row["id"],
                    "round_index": row["round_index"],
                    "agent": row["agent"],
                    "summary": row["summary"],
                    "artifact_uri": row["artifact_uri"],
                    "blocking_issues": json.loads(row["blocking_issues_json"]),
                    "confidence": row["confidence"],
                    "created_at": row["created_at"],
                }
                for row in rounds
            ],
            "decisions": [dict(row) for row in decisions],
        }
