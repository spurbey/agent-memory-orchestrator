from __future__ import annotations

from pathlib import Path
from typing import Any

from .validation import bounded_limit as _bounded_limit
from .validation import normalize_agent as _normalize_agent
from .validation import parse_metadata as _parse_metadata
from .validation import require_text as _require_text


class MemoryToolMixin:
    def memory_write(
        self,
        *,
        session_id: str,
        agent: str,
        event_type: str,
        content: str,
        metadata_json: str = "{}",
        create_memory: bool = True,
    ) -> dict[str, Any]:
        session_id = _require_text(session_id, "session_id")
        agent = _normalize_agent(agent)
        event_type = _require_text(event_type, "event_type")
        content = _require_text(content, "content")
        metadata = _parse_metadata(metadata_json)

        if not self.memory.session_exists(session_id):
            self.memory.create_session(session_id=session_id, title=session_id)
        event = self.memory.add_event(
            session_id=session_id,
            agent=agent,
            event_type=event_type,
            content=content,
            metadata=metadata,
            source_app=agent,
            process=create_memory,
        )
        rows = self.memory.conn.execute(
            """
            SELECT id
            FROM memory_units
            WHERE source_event_id = ?
            ORDER BY created_at DESC
            """,
            (event.id,),
        ).fetchall()
        memory_ids = [row["id"] for row in rows]
        return {
            "ok": True,
            "event_id": event.id,
            "session_id": event.session_id,
            "memory_ids": memory_ids,
            "memory_id": memory_ids[0] if memory_ids else None,
            "memory_count": len(memory_ids),
            "redacted": event.redacted,
        }

    def memory_search(
        self,
        *,
        query: str,
        session_id: str = "",
        limit: int = 10,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        query = _require_text(query, "query")
        target_session = session_id or None
        safe_limit = _bounded_limit(limit, default=10, maximum=100)
        results = self.memory.search_memories(
            query=query,
            session_id=target_session,
            limit=safe_limit,
            include_historical=include_historical,
        )
        return {"ok": True, "count": len(results), "results": results}

    def memory_context_pack(
        self,
        *,
        query: str,
        session_id: str = "",
        budget: int = 2500,
        limit: int = 12,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        query = _require_text(query, "query")
        return {
            "ok": True,
            **self.memory.build_context_pack(
                query=query,
                session_id=(session_id or None),
                budget_tokens=max(1, int(budget)),
                limit=_bounded_limit(limit, default=12, maximum=100),
                include_historical=include_historical,
            ),
        }

    def memory_metrics(self) -> dict[str, Any]:
        return {"ok": True, "metrics": self.memory.inspect_metrics()}

    def memory_rebuild_indexes(self, *, force_vectors: bool = False) -> dict[str, Any]:
        return {"ok": True, "result": self.memory.rebuild_indexes(force_vectors=force_vectors)}

    def memory_timeline(self, *, session_id: str, limit: int = 50) -> dict[str, Any]:
        session_id = _require_text(session_id, "session_id")
        events = self.memory.timeline(session_id=session_id, limit=_bounded_limit(limit, default=50, maximum=500))
        return {"ok": True, "count": len(events), "events": events}

    def memory_export(self, *, out_path: str = "", session_id: str = "") -> dict[str, Any]:
        target = Path(out_path) if out_path else self.settings.export_dir / "memory_snapshot.jsonl"
        rows = self.memory.export_snapshot(out_path=target, session_id=(session_id or None))
        return {"ok": True, "rows": rows, "out_path": str(target.resolve())}

    def memory_import(self, *, in_path: str) -> dict[str, Any]:
        source = Path(_require_text(in_path, "in_path"))
        rows = self.memory.import_snapshot(source)
        indexes = self.memory.rebuild_indexes(force_vectors=False)
        return {"ok": True, "rows": rows, "in_path": str(source.resolve()), "indexes": indexes}


__all__ = ["MemoryToolMixin"]
