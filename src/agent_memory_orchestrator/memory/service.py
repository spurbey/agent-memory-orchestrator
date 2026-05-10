from __future__ import annotations

import json
import re
import sqlite3

from ..config import Settings
from ..db import connect, init_schema
from .common import preview as _preview
from .common import stable_json as _json
from .common import utc_now as _utc_now
from .ingest import MemoryIngestMixin
from .pipeline import MemoryPipelineMixin
from .retrieval import MemoryRetrievalMixin
from .snapshots import MemorySnapshotMixin
from .storage import MemoryStorageMixin
from .views import MemoryViewsMixin

class MemoryService(
    MemoryIngestMixin,
    MemoryStorageMixin,
    MemoryRetrievalMixin,
    MemoryViewsMixin,
    MemorySnapshotMixin,
    MemoryPipelineMixin,
):
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


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"
