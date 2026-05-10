from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..config import Settings
from ..db import connect, init_schema
from .common import elapsed_ms as _elapsed_ms
from .common import new_id as _id
from .common import preview as _preview
from .common import stable_json as _json
from .common import utc_now as _utc_now
from .ingest import MemoryIngestMixin
from .retrieval import MemoryRetrievalMixin
from .storage import MemoryStorageMixin
from .views import MemoryViewsMixin

class MemoryService(MemoryIngestMixin, MemoryStorageMixin, MemoryRetrievalMixin, MemoryViewsMixin):
    def __init__(self, settings: Settings, conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings
        self.conn = conn or connect(settings.db_path)
        self.defer_vectors = False

    def init_db(self) -> None:
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    def build_hook_context(self, payload: dict, default_agent: str = "codex", limit: int = 6) -> str:
        event_name = str(payload.get("hook_event_name") or payload.get("event_type") or "")
        normalized_name = _snake(event_name)
        if normalized_name == "session_start":
            summaries = self.list_sessions(limit=5)
            if not summaries:
                return ""
            lines = ["AMO local memory context: recent session summaries:"]
            for row in summaries[:5]:
                summary = _preview(row.get("summary_text") or "No summary yet.", 280)
                lines.append(f"- session={row['id']} status={row['status']}: {summary}")
            return "\n".join(lines)

        if normalized_name != "user_prompt_submit":
            return ""

        prompt = str(payload.get("prompt") or payload.get("content") or "")
        if not prompt.strip():
            return ""
        pack = self.build_context_pack(prompt, session_id=None, budget_tokens=self.settings.context_budget, limit=limit)
        return pack["text"] if pack["items"] else ""

    def codex_hook_response(self, payload: dict, default_agent: str = "codex") -> dict:
        event_name = str(payload.get("hook_event_name") or payload.get("event_type") or "")
        normalized_name = _snake(event_name)
        additional_context = self.build_hook_context(payload, default_agent=default_agent)

        hook_event_name = {
            "session_start": "SessionStart",
            "user_prompt_submit": "UserPromptSubmit",
            "post_tool_use": "PostToolUse",
            "stop": "Stop",
        }.get(normalized_name, event_name or "UserPromptSubmit")

        response: dict[str, object] = {"continue": True}
        if additional_context and self.settings.approval_mode == "auto_safe":
            response["hookSpecificOutput"] = {
                "hookEventName": hook_event_name,
                "additionalContext": additional_context,
            }
        elif additional_context:
            response["systemMessage"] = "AMO captured this turn. Memory context injection is disabled unless AMO_APPROVAL_MODE=auto_safe."

        try:
            # Hooks must stay below Codex's timeout. Store raw evidence now;
            # chunking, extraction, embeddings, and consolidation belong in
            # daemon/background processing, not the prompt submission hot path.
            self.ingest_hook_payload(payload, default_agent=default_agent, process=False)
        except Exception as exc:
            message = f"AMO hook capture failed open: {exc}"
            existing = str(response.get("systemMessage") or "")
            response["systemMessage"] = f"{existing}\n{message}".strip() if existing else message
        return response

    def generate_session_summary(self, session_id: str) -> dict:
        rows = self.conn.execute(
            """
            SELECT id, memory_type, summary, entities_json
            FROM memory_units
            WHERE session_id = ? AND status = 'active'
            ORDER BY created_at ASC
            """,
            (session_id,),
        ).fetchall()
        decisions = [row["summary"] for row in rows if row["memory_type"] == "decision"][:8]
        blockers = [row["summary"] for row in rows if row["memory_type"] in {"bug", "blocker"}][:8]
        changed_files: list[str] = []
        evidence_ids = [row["id"] for row in rows]
        for row in rows:
            for entity in json.loads(row["entities_json"]):
                if "." in entity and entity not in changed_files:
                    changed_files.append(entity)
        summary_parts = []
        if decisions:
            summary_parts.append("Decisions: " + " | ".join(decisions[:5]))
        if changed_files:
            summary_parts.append("Changed files/entities: " + ", ".join(changed_files[:12]))
        if blockers:
            summary_parts.append("Open blockers/bugs: " + " | ".join(blockers[:5]))
        summary_text = "\n".join(summary_parts) or "No durable memory extracted yet."
        ts = _utc_now()
        self.conn.execute(
            """
            INSERT INTO session_summaries(
              session_id, summary_text, key_decisions_json, open_blockers_json,
              changed_files_json, evidence_memory_ids_json, generator, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
              summary_text=excluded.summary_text,
              key_decisions_json=excluded.key_decisions_json,
              open_blockers_json=excluded.open_blockers_json,
              changed_files_json=excluded.changed_files_json,
              evidence_memory_ids_json=excluded.evidence_memory_ids_json,
              updated_at=excluded.updated_at
            """,
            (
                session_id,
                summary_text,
                _json(decisions),
                _json(blockers),
                _json(changed_files),
                _json(evidence_ids),
                "rule_v1",
                ts,
                ts,
            ),
        )
        self.conn.commit()
        return {
            "session_id": session_id,
            "summary_text": summary_text,
            "key_decisions": decisions,
            "open_blockers": blockers,
            "changed_files": changed_files,
        }

    def export_snapshot(self, out_path: Path, session_id: str | None = None) -> int:
        tables = [
            "sessions",
            "events",
            "chunks",
            "memory_units",
            "memories",
            "memory_vectors",
            "entities",
            "kg_edges",
            "session_summaries",
            "index_versions",
            "pipeline_runs",
            "extraction_runs",
            "retrieval_runs",
            "retrieval_candidates",
            "consolidation_decisions",
            "approval_decisions",
            "orchestration_rounds",
            "orchestration_decisions",
        ]
        rows_written = 0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as f:
            for table in tables:
                for row in self._rows_for_export(table, session_id):
                    f.write(_json({"table": table, "row": dict(row)}) + "\n")
                    rows_written += 1
        return rows_written

    def import_snapshot(self, in_path: Path) -> int:
        inserted = 0
        with in_path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                table = payload.get("table")
                row = payload.get("row")
                if not table or not isinstance(row, dict):
                    continue
                columns = list(row.keys())
                col_clause = ", ".join(columns)
                val_clause = ", ".join(["?"] * len(columns))
                self.conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_clause}) VALUES ({val_clause})",
                    tuple(row[col] for col in columns),
                )
                inserted += 1
        self.conn.commit()
        return inserted

    def _start_pipeline_run(self, run_type: str, session_id: str | None, source_event_id: str | None) -> str:
        run_id = _id("prun")
        self.conn.execute(
            """
            INSERT INTO pipeline_runs(id, run_type, session_id, source_event_id, status, started_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (run_id, run_type, session_id, source_event_id, "running", _utc_now()),
        )
        self.conn.commit()
        return run_id

    def _finish_pipeline_run(
        self,
        run_id: str,
        status: str,
        started: float,
        metrics: dict,
        error: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, finished_at = ?, duration_ms = ?, metrics_json = ?, error = ?
            WHERE id = ?
            """,
            (status, _utc_now(), _elapsed_ms(started), _json(metrics), error, run_id),
        )
        self.conn.commit()

    def _rows_for_export(self, table: str, session_id: str | None) -> list[sqlite3.Row]:
        if not session_id:
            return list(self.conn.execute(f"SELECT * FROM {table}").fetchall())
        if table == "sessions":
            return list(self.conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchall())
        session_tables = {
            "events",
            "chunks",
            "memory_units",
            "memories",
            "session_summaries",
            "pipeline_runs",
            "extraction_runs",
            "retrieval_runs",
            "approval_decisions",
            "orchestration_rounds",
            "orchestration_decisions",
        }
        if table in session_tables:
            return list(self.conn.execute(f"SELECT * FROM {table} WHERE session_id = ?", (session_id,)).fetchall())
        if table == "memory_vectors":
            return list(
                self.conn.execute(
                    """
                    SELECT mv.*
                    FROM memory_vectors mv
                    JOIN memory_units mu ON mu.id = mv.memory_id
                    WHERE mu.session_id = ?
                    """,
                    (session_id,),
                ).fetchall()
            )
        return list(self.conn.execute(f"SELECT * FROM {table}").fetchall())


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"
