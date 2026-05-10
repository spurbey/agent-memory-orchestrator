from __future__ import annotations

import re
import sqlite3

from ..config import Settings
from ..db import connect, init_schema
from .common import preview as _preview
from .ingest import MemoryIngestMixin
from .pipeline import MemoryPipelineMixin
from .retrieval import MemoryRetrievalMixin
from .snapshots import MemorySnapshotMixin
from .storage import MemoryStorageMixin
from .summary import MemorySummaryMixin
from .views import MemoryViewsMixin

class MemoryService(
    MemoryIngestMixin,
    MemoryStorageMixin,
    MemoryRetrievalMixin,
    MemoryViewsMixin,
    MemorySnapshotMixin,
    MemoryPipelineMixin,
    MemorySummaryMixin,
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


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"
