from __future__ import annotations

import json
import time
from pathlib import Path

from .processing.chunker import chunk_text
from .processing.cleaning import clean_event_text
from .processing.extraction import extract_memory_candidates
from ..integrations.adapters import infer_codex_session, normalize_adapter_event
from ..core.models import Chunk, Event, MemoryUnit
from ..core.privacy import redact_secrets
from .common import new_id as _id
from .common import stable_json as _json
from .common import utc_now as _utc_now


class MemoryIngestMixin:
    def create_session(self, session_id: str, title: str, status: str = "draft") -> None:
        ts = _utc_now()
        self.conn.execute(
            """
            INSERT INTO sessions(
              id, title, status, owner_user_id, workspace_id, project_id,
              visibility_scope, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              owner_user_id=excluded.owner_user_id,
              workspace_id=excluded.workspace_id,
              project_id=excluded.project_id,
              visibility_scope=excluded.visibility_scope,
              updated_at=excluded.updated_at
            """,
            (
                session_id,
                title,
                status,
                self.settings.owner_user_id,
                self.settings.workspace_id,
                self.settings.project_id,
                self.settings.visibility_scope,
                ts,
                ts,
            ),
        )
        self.conn.commit()

    def session_exists(self, session_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return row is not None

    def _session_has_events(self, session_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM events WHERE session_id = ? LIMIT 1", (session_id,)).fetchone()
        return row is not None

    def add_event(
        self,
        session_id: str,
        agent: str,
        event_type: str,
        content: str,
        metadata: dict | None = None,
        created_at: str | None = None,
        event_id: str | None = None,
        *,
        source_app: str = "unknown",
        process: bool = False,
    ) -> Event:
        if not self.session_exists(session_id):
            self.create_session(session_id, title=session_id)

        ts = created_at or _utc_now()
        eid = event_id or _id("evt")
        payload = metadata or {}
        clean_content, redacted = redact_secrets(content)
        self.conn.execute(
            """
            INSERT INTO events(
              id, session_id, agent, event_type, content, metadata_json, source_app,
              owner_user_id, workspace_id, project_id, visibility_scope,
              sensitivity_level, redacted, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                eid,
                session_id,
                agent,
                event_type,
                clean_content,
                _json(payload),
                source_app,
                self.settings.owner_user_id,
                self.settings.workspace_id,
                self.settings.project_id,
                self.settings.visibility_scope,
                self.settings.sensitivity_level,
                1 if redacted else 0,
                ts,
            ),
        )
        self.conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (ts, session_id))
        self.conn.commit()
        event = Event(
            id=eid,
            session_id=session_id,
            agent=agent,
            event_type=event_type,
            content=clean_content,
            metadata=payload,
            created_at=ts,
            source_app=source_app,
            owner_user_id=self.settings.owner_user_id,
            workspace_id=self.settings.workspace_id,
            project_id=self.settings.project_id,
            visibility_scope=self.settings.visibility_scope,
            sensitivity_level=self.settings.sensitivity_level,
            redacted=redacted,
        )
        if process:
            self.process_event(event)
        return event

    def process_event(self, event: Event) -> dict[str, int]:
        run_id = self._start_pipeline_run("ingest_event", event.session_id, event.id)
        started = time.perf_counter()
        chunks_count = 0
        memory_count = 0
        suppressed_chunks = 0
        try:
            chunks = self.create_chunks_for_event(event)
            chunks_count = len(chunks)
            suppressed_chunks = sum(1 for chunk in chunks if chunk.metadata.get("amo_promote_memory") is False)
            for chunk in chunks:
                memory_count += len(self.extract_memories_for_chunk(event, chunk, run_id))
            if event.event_type.lower() in {"stop", "session_stop"}:
                self.generate_session_summary(event.session_id)
            self._finish_pipeline_run(
                run_id,
                "completed",
                started,
                {
                    "chunks": chunks_count,
                    "memory_units": memory_count,
                    "suppressed_memory_chunks": suppressed_chunks,
                    "cleanup_reasons": _cleanup_reason_counts(chunks),
                },
            )
            return {"chunks": chunks_count, "memory_units": memory_count, "suppressed_memory_chunks": suppressed_chunks}
        except Exception as exc:
            self._finish_pipeline_run(run_id, "failed", started, {"chunks": chunks_count}, str(exc))
            raise

    def create_chunks_for_event(self, event: Event) -> list[Chunk]:
        cleaned = clean_event_text(
            event.content,
            event_type=event.event_type,
            agent=event.agent,
            metadata=event.metadata,
        )
        candidates = chunk_text(cleaned.text, event.event_type, cleaned.metadata)
        chunks: list[Chunk] = []
        ts = _utc_now()
        for idx, candidate in enumerate(candidates):
            cid = _id("chk")
            chunk_metadata = {**cleaned.metadata, **candidate.metadata}
            self.conn.execute(
                """
                INSERT OR IGNORE INTO chunks(
                  id, session_id, event_id, chunk_index, content_type, text,
                  token_count, content_hash, metadata_json, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid,
                    event.session_id,
                    event.id,
                    idx,
                    candidate.content_type,
                    candidate.text,
                    candidate.token_count,
                    candidate.content_hash,
                    _json(chunk_metadata),
                    ts,
                ),
            )
            chunks.append(
                Chunk(
                    id=cid,
                    session_id=event.session_id,
                    event_id=event.id,
                    chunk_index=idx,
                    content_type=candidate.content_type,
                    text=candidate.text,
                    token_count=candidate.token_count,
                    content_hash=candidate.content_hash,
                    metadata=chunk_metadata,
                    created_at=ts,
                )
            )
        self.conn.commit()
        return chunks

    def extract_memories_for_chunk(self, event: Event, chunk: Chunk, pipeline_run_id: str | None = None) -> list[MemoryUnit]:
        candidates = extract_memory_candidates(
            chunk.text,
            content_type=chunk.content_type,
            event_type=event.event_type,
            agent=event.agent,
            metadata=chunk.metadata,
        )
        units: list[MemoryUnit] = []
        confidence_debug: dict[str, float] = {}
        for candidate in candidates:
            unit = self.add_memory_unit(
                session_id=event.session_id,
                source_event_id=event.id,
                source_chunk_id=chunk.id,
                memory_type=candidate.memory_type,
                subject=candidate.subject,
                predicate=candidate.predicate,
                object_text=candidate.object,
                summary=candidate.summary,
                topic_key=candidate.topic_key,
                entities=candidate.entities,
                tags=candidate.tags,
                confidence=candidate.confidence,
                importance=candidate.importance,
            )
            units.append(unit)
            confidence_debug[unit.id] = candidate.confidence

        self.conn.execute(
            """
            INSERT INTO extraction_runs(
              id, pipeline_run_id, session_id, source_chunk_id, extractor,
              memory_count, confidence_json, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _id("xrun"),
                pipeline_run_id,
                event.session_id,
                chunk.id,
                "rule_v1",
                len(units),
                _json(confidence_debug),
                _utc_now(),
            ),
        )
        self.conn.commit()
        return units

    def ingest_transcript(
        self,
        agent: str,
        file_path: Path,
        session_id: str,
        session_title: str | None = None,
    ) -> dict[str, int]:
        if not self.session_exists(session_id):
            self.create_session(session_id, title=session_title or session_id)

        events_count = 0
        chunks_count = 0
        memories_count = 0
        suppressed_chunks_count = 0
        with file_path.open("r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                normalized = self.normalize_event_payload(item, default_agent=agent, default_session_id=session_id)
                if normalized is None:
                    continue
                event = self.add_event(
                    session_id=normalized["session_id"],
                    agent=normalized["agent"],
                    event_type=normalized["event_type"],
                    content=normalized["content"],
                    metadata=normalized["metadata"],
                    created_at=normalized.get("created_at"),
                    source_app=normalized["source_app"],
                    process=False,
                )
                counts = self.process_event(event)
                events_count += 1
                chunks_count += counts["chunks"]
                memories_count += counts["memory_units"]
                suppressed_chunks_count += counts.get("suppressed_memory_chunks", 0)

        self.generate_session_summary(session_id)
        return {
            "events": events_count,
            "chunks": chunks_count,
            "memories": memories_count,
            "memory_units": memories_count,
            "suppressed_memory_chunks": suppressed_chunks_count,
        }

    def import_codex_sessions(
        self,
        root: Path,
        limit: int = 30,
        *,
        defer_vectors: bool = False,
        skip_existing: bool = True,
    ) -> dict[str, object]:
        files = sorted(root.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
        selected = files[:limit] if limit > 0 else files
        imported: list[dict[str, object]] = []
        skipped: list[dict[str, object]] = []
        totals = {
            "files": 0,
            "skipped_existing": 0,
            "events": 0,
            "chunks": 0,
            "memory_units": 0,
            "suppressed_memory_chunks": 0,
        }
        previous_defer = self.defer_vectors
        self.defer_vectors = defer_vectors
        try:
            for file_path in selected:
                session_id, title = self._infer_codex_session(file_path)
                if skip_existing and self._session_has_events(session_id):
                    totals["skipped_existing"] += 1
                    skipped.append({"file": str(file_path), "session_id": session_id, "reason": "session_already_imported"})
                    continue
                result = self.ingest_transcript(
                    agent="codex",
                    file_path=file_path,
                    session_id=session_id,
                    session_title=title,
                )
                totals["files"] += 1
                totals["events"] += int(result["events"])
                totals["chunks"] += int(result["chunks"])
                totals["memory_units"] += int(result["memory_units"])
                totals["suppressed_memory_chunks"] += int(result.get("suppressed_memory_chunks", 0))
                imported.append({"file": str(file_path), "session_id": session_id, **result})
        finally:
            self.defer_vectors = previous_defer
        return {
            "root": str(root),
            "defer_vectors": defer_vectors,
            "skip_existing": skip_existing,
            "totals": totals,
            "imported": imported,
            "skipped": skipped,
        }

    def ingest_hook_payload(
        self,
        payload: dict,
        default_agent: str = "codex",
        *,
        process: bool = True,
    ) -> dict[str, object]:
        normalized = self.normalize_event_payload(payload, default_agent=default_agent)
        if normalized is None:
            return {"event_id": None, "session_id": payload.get("session_id") or "default", "skipped": True}
        if not self.session_exists(normalized["session_id"]):
            self.create_session(normalized["session_id"], normalized["session_id"])
        event = self.add_event(
            session_id=normalized["session_id"],
            agent=normalized["agent"],
            event_type=normalized["event_type"],
            content=normalized["content"],
            metadata=normalized["metadata"],
            created_at=normalized.get("created_at"),
            source_app=normalized["source_app"],
            process=process,
        )
        if process and event.event_type.lower() in {"stop", "session_stop"}:
            self.generate_session_summary(event.session_id)
        return {"event_id": event.id, "session_id": event.session_id}

    def normalize_event_payload(
        self,
        item: dict,
        *,
        default_agent: str = "system",
        default_session_id: str | None = None,
    ) -> dict[str, object] | None:
        return normalize_adapter_event(
            item,
            default_agent=default_agent,
            default_session_id=default_session_id,
        )

    def _infer_codex_session(self, file_path: Path) -> tuple[str, str]:
        return infer_codex_session(file_path)



def _cleanup_reason_counts(chunks: list[Chunk]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        reason = str(chunk.metadata.get("amo_suppression_reason") or "")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


