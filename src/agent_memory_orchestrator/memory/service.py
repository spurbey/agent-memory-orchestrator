from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path

from ..chunker import chunk_text
from ..cleaning import clean_event_text
from ..config import Settings
from ..db import connect, init_schema
from ..extraction import extract_memory_candidates, extract_tags, make_topic_key
from ..integrations.adapters import infer_codex_session, normalize_adapter_event
from ..llm.embeddings import cosine_similarity, embed_text_with_model
from ..llm.vector_cache import VectorRow, build_faiss_cache
from ..models import Chunk, Event, Memory, MemoryUnit
from ..privacy import redact_secrets
from .common import elapsed_ms as _elapsed_ms
from .common import new_id as _id
from .common import stable_json as _json
from .common import utc_now as _utc_now
from .retrieval import MemoryRetrievalMixin, _ranking_terms
from ..retrieval import lexical_rerank_score

class MemoryService(MemoryRetrievalMixin):
    def __init__(self, settings: Settings, conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings
        self.conn = conn or connect(settings.db_path)
        self.defer_vectors = False

    def init_db(self) -> None:
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

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

    def add_memory_unit(
        self,
        *,
        session_id: str,
        source_event_id: str,
        source_chunk_id: str | None,
        memory_type: str,
        subject: str,
        predicate: str,
        object_text: str,
        summary: str,
        topic_key: str,
        entities: list[str] | None = None,
        tags: list[str] | None = None,
        confidence: float = 0.4,
        importance: float = 0.5,
        status: str = "active",
        memory_id: str | None = None,
    ) -> MemoryUnit:
        ts = _utc_now()
        mid = memory_id or _id("mem")
        entity_list = entities or []
        tag_list = tags or []
        self.conn.execute(
            """
            INSERT INTO memory_units(
              id, session_id, source_event_id, source_chunk_id, memory_type,
              subject, predicate, object, summary, topic_key, entities_json,
              tags_json, confidence, importance, status, owner_user_id,
              workspace_id, project_id, visibility_scope, sensitivity_level,
              created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mid,
                session_id,
                source_event_id,
                source_chunk_id,
                memory_type,
                subject,
                predicate,
                object_text,
                summary,
                topic_key,
                _json(entity_list),
                _json(tag_list),
                confidence,
                importance,
                status,
                self.settings.owner_user_id,
                self.settings.workspace_id,
                self.settings.project_id,
                self.settings.visibility_scope,
                self.settings.sensitivity_level,
                ts,
                ts,
            ),
        )
        self._mirror_legacy_memory(mid, session_id, source_event_id, summary, tag_list, importance, ts)
        if not self.defer_vectors:
            self._write_vector(mid, summary)
        self._write_fts(mid, summary, subject, object_text, topic_key)
        self._write_kg_for_memory(mid, source_chunk_id, subject, entity_list, memory_type, confidence)
        self._consolidate_memory(mid, topic_key)
        self.conn.commit()
        return MemoryUnit(
            id=mid,
            session_id=session_id,
            source_event_id=source_event_id,
            source_chunk_id=source_chunk_id,
            memory_type=memory_type,
            subject=subject,
            predicate=predicate,
            object=object_text,
            summary=summary,
            topic_key=topic_key,
            entities=entity_list,
            tags=tag_list,
            confidence=confidence,
            importance=importance,
            status=status,
            created_at=ts,
        )

    def add_memory(
        self,
        session_id: str,
        source_event_id: str,
        summary: str,
        tags: list[str] | None = None,
        importance: float = 0.5,
        vector: list[float] | None = None,
        created_at: str | None = None,
        memory_id: str | None = None,
    ) -> Memory:
        tag_list = tags or extract_tags(summary)
        subject = tag_list[0] if tag_list else "session"
        mid = memory_id or _id("mem")
        unit = self.add_memory_unit(
            session_id=session_id,
            source_event_id=source_event_id,
            source_chunk_id=None,
            memory_type="observation",
            subject=subject,
            predicate="observes",
            object_text=summary,
            summary=summary,
            topic_key=make_topic_key(subject, tag_list),
            entities=[],
            tags=tag_list,
            confidence=0.4,
            importance=importance,
            memory_id=mid,
        )
        if vector is not None:
            ts = created_at or _utc_now()
            self.conn.execute(
                """
                INSERT INTO memory_vectors(memory_id, dims, model, backend, vector_json, created_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                  dims=excluded.dims,
                  model=excluded.model,
                  backend=excluded.backend,
                  vector_json=excluded.vector_json,
                  created_at=excluded.created_at
                """,
                (unit.id, len(vector), "provided", "sqlite", _json(vector), ts),
            )
            self.conn.commit()
        return Memory(unit.id, session_id, source_event_id, summary, tag_list, importance, unit.created_at)

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

    def timeline(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, session_id, agent, event_type, content, metadata_json, source_app,
                   visibility_scope, sensitivity_level, redacted, created_at
            FROM events
            WHERE session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (session_id, limit),
        ).fetchall()

        return [
            {
                "id": row["id"],
                "session_id": row["session_id"],
                "agent": row["agent"],
                "event_type": row["event_type"],
                "content": row["content"],
                "metadata": json.loads(row["metadata_json"]),
                "source_app": row["source_app"],
                "visibility_scope": row["visibility_scope"],
                "sensitivity_level": row["sensitivity_level"],
                "redacted": bool(row["redacted"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]

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

    def inspect_metrics(self) -> dict:
        tables = [
            "sessions",
            "events",
            "chunks",
            "memory_units",
            "entities",
            "kg_edges",
            "pipeline_runs",
            "retrieval_runs",
            "retrieval_candidates",
            "consolidation_decisions",
        ]
        counts = {}
        for table in tables:
            counts[table] = self.conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"]
        latest_retrieval = self.conn.execute(
            "SELECT * FROM retrieval_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return {"counts": counts, "latest_retrieval": dict(latest_retrieval) if latest_retrieval else None}

    def dashboard_snapshot(self, limit: int = 25) -> dict:
        return {
            "metrics": self.inspect_metrics(),
            "sessions": self.list_sessions(limit=limit),
            "recent_events": self.list_events(limit=limit),
            "recent_memories": self.list_memory_units(limit=limit, include_historical=True),
            "retrieval_runs": self.list_retrieval_runs(limit=limit),
        }

    def graph_snapshot(
        self,
        *,
        query: str | None = None,
        session_id: str | None = None,
        limit: int = 100,
        include_historical: bool = False,
        relation: str | None = None,
        node_type: str | None = None,
        memory_type: str | None = None,
        min_confidence: float | None = None,
    ) -> dict:
        clauses = []
        params: list[object] = []
        if session_id:
            clauses.append("mu.session_id = ?")
            params.append(session_id)
        if not include_historical:
            clauses.append("ke.status = 'active'")
            clauses.append("(mu.status IS NULL OR mu.status = 'active')")
        if relation:
            clauses.append("ke.relation = ?")
            params.append(relation)
        if node_type:
            clauses.append("(se.entity_type = ? OR te.entity_type = ?)")
            params.extend([node_type, node_type])
        if memory_type:
            clauses.append("mu.memory_type = ?")
            params.append(memory_type)
        if min_confidence is not None:
            clauses.append("ke.confidence >= ?")
            params.append(float(min_confidence))
        if query:
            like = f"%{query.strip()}%"
            clauses.append(
                """
                (
                  se.name LIKE ? OR se.normalized_name LIKE ? OR
                  te.name LIKE ? OR te.normalized_name LIKE ? OR
                  mu.summary LIKE ? OR mu.subject LIKE ? OR mu.object LIKE ? OR
                  mu.topic_key LIKE ? OR mu.entities_json LIKE ? OR mu.tags_json LIKE ?
                )
                """
            )
            params.extend([like] * 10)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(max(1, limit))
        rows = self.conn.execute(
            f"""
            SELECT
              ke.id AS edge_id,
              ke.relation,
              ke.status AS edge_status,
              ke.confidence AS edge_confidence,
              ke.created_at AS edge_created_at,
              ke.evidence_memory_id,
              ke.evidence_chunk_id,
              se.id AS source_id,
              se.name AS source_name,
              se.entity_type AS source_type,
              te.id AS target_id,
              te.name AS target_name,
              te.entity_type AS target_type,
              mu.id AS memory_id,
              mu.session_id,
              mu.memory_type,
              mu.subject,
              mu.summary,
              mu.topic_key,
              mu.status AS memory_status,
              mu.confidence AS memory_confidence
            FROM kg_edges ke
            JOIN entities se ON se.id = ke.source_entity_id
            JOIN entities te ON te.id = ke.target_entity_id
            LEFT JOIN memory_units mu ON mu.id = ke.evidence_memory_id
            {where}
            ORDER BY ke.created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        relation_counts: dict[str, int] = {}
        type_counts: dict[str, int] = {}
        memory_ids: set[str] = set()

        for row in rows:
            source = _graph_node(row["source_id"], row["source_name"], row["source_type"])
            target = _graph_node(row["target_id"], row["target_name"], row["target_type"])
            if row["source_type"] == "memory" and row["source_name"] == row["memory_id"]:
                source.update(_graph_memory_fields(row))
            if row["target_type"] == "memory" and row["target_name"] == row["memory_id"]:
                target.update(_graph_memory_fields(row))
            nodes[source["id"]] = {**nodes.get(source["id"], {}), **source}
            nodes[target["id"]] = {**nodes.get(target["id"], {}), **target}
            relation = str(row["relation"])
            relation_counts[relation] = relation_counts.get(relation, 0) + 1
            type_counts[source["type"]] = type_counts.get(source["type"], 0) + 1
            type_counts[target["type"]] = type_counts.get(target["type"], 0) + 1
            if row["memory_id"]:
                memory_ids.add(row["memory_id"])
            edges.append(
                {
                    "id": row["edge_id"],
                    "source": row["source_id"],
                    "target": row["target_id"],
                    "relation": relation,
                    "status": row["edge_status"],
                    "confidence": row["edge_confidence"],
                    "evidence_memory_id": row["evidence_memory_id"],
                    "evidence_chunk_id": row["evidence_chunk_id"],
                    "created_at": row["edge_created_at"],
                }
            )

        return {
            "query": query or "",
            "session_id": session_id,
            "include_historical": include_historical,
            "filters": {
                "relation": relation or "",
                "node_type": node_type or "",
                "memory_type": memory_type or "",
                "min_confidence": min_confidence,
            },
            "limit": limit,
            "nodes": list(nodes.values()),
            "edges": edges,
            "stats": {
                "node_count": len(nodes),
                "edge_count": len(edges),
                "evidence_memory_count": len(memory_ids),
                "relation_counts": relation_counts,
                "node_type_counts": type_counts,
            },
        }

    def list_sessions(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
              s.id,
              s.title,
              s.status,
              s.owner_user_id,
              s.project_id,
              s.visibility_scope,
              s.created_at,
              s.updated_at,
              (
                SELECT COUNT(*)
                FROM events e
                WHERE e.session_id = s.id
              ) AS event_count,
              (
                SELECT COUNT(*)
                FROM memory_units mu
                WHERE mu.session_id = s.id
              ) AS memory_count,
              ss.summary_text AS summary_text
            FROM sessions s
            LEFT JOIN session_summaries ss ON ss.session_id = s.id
            ORDER BY s.updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_events(self, session_id: str | None = None, limit: int = 50) -> list[dict]:
        session_clause = "WHERE session_id = ?" if session_id else ""
        params: tuple[object, ...] = (session_id, limit) if session_id else (limit,)
        rows = self.conn.execute(
            f"""
            SELECT id, session_id, agent, event_type, content, source_app,
                   visibility_scope, sensitivity_level, redacted, created_at
            FROM events
            {session_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [
            {
                **dict(row),
                "redacted": bool(row["redacted"]),
                "content_preview": _preview(row["content"], 500),
            }
            for row in rows
        ]

    def list_memory_units(
        self,
        session_id: str | None = None,
        limit: int = 50,
        include_historical: bool = True,
    ) -> list[dict]:
        clauses = []
        params: list[object] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if not include_historical:
            clauses.append("status = 'active'")
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"""
            SELECT id, session_id, source_event_id, source_chunk_id, memory_type,
                   subject, predicate, object, summary, topic_key, entities_json,
                   tags_json, confidence, importance, status, supersedes_memory_id,
                   created_at, updated_at
            FROM memory_units
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()
        return [
            {
                **dict(row),
                "entities": json.loads(row["entities_json"]),
                "tags": json.loads(row["tags_json"]),
                "summary_preview": _preview(row["summary"], 500),
            }
            for row in rows
        ]

    def list_retrieval_runs(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT
              rr.id,
              rr.query,
              rr.intent,
              rr.session_id,
              rr.include_historical,
              rr.status,
              rr.started_at,
              rr.finished_at,
              rr.duration_ms,
              rr.config_json,
              COUNT(rc.id) AS candidate_count,
              MAX(rc.final_score) AS top_score
            FROM retrieval_runs rr
            LEFT JOIN retrieval_candidates rc ON rc.retrieval_run_id = rr.id
            GROUP BY rr.id
            ORDER BY rr.started_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [
            {
                **dict(row),
                "include_historical": bool(row["include_historical"]),
                "config": json.loads(row["config_json"]),
            }
            for row in rows
        ]

    def retrieval_run_detail(self, retrieval_run_id: str) -> dict:
        run = self.conn.execute(
            "SELECT * FROM retrieval_runs WHERE id = ?",
            (retrieval_run_id,),
        ).fetchone()
        if run is None:
            raise ValueError(f"retrieval run does not exist: {retrieval_run_id}")
        candidates = self.conn.execute(
            """
            SELECT
              rc.id,
              rc.memory_id,
              rc.source,
              rc.rank,
              rc.raw_score,
              rc.rrf_score,
              rc.rerank_score,
              rc.final_score,
              rc.score_breakdown_json,
              rc.created_at,
              mu.session_id,
              mu.memory_type,
              mu.summary,
              mu.subject,
              mu.predicate,
              mu.object,
              mu.topic_key,
              mu.status,
              mu.confidence,
              mu.source_event_id,
              mu.source_chunk_id
            FROM retrieval_candidates rc
            JOIN memory_units mu ON mu.id = rc.memory_id
            WHERE rc.retrieval_run_id = ?
            ORDER BY rc.rank ASC
            """,
            (retrieval_run_id,),
        ).fetchall()
        return {
            "run": {
                **dict(run),
                "include_historical": bool(run["include_historical"]),
                "config": json.loads(run["config_json"]),
            },
            "candidates": [
                {
                    **dict(row),
                    "score_breakdown": json.loads(row["score_breakdown_json"]),
                    "summary_preview": _preview(row["summary"], 700),
                }
                for row in candidates
            ],
        }

    def rebuild_indexes(self, force_vectors: bool = False) -> dict:
        try:
            self.conn.execute("DELETE FROM memory_units_fts")
        except sqlite3.OperationalError:
            pass
        rows = self.conn.execute(
            "SELECT id, summary, subject, object, topic_key FROM memory_units ORDER BY created_at ASC"
        ).fetchall()
        vectors_written = 0
        for row in rows:
            self._write_fts(row["id"], row["summary"], row["subject"], row["object"], row["topic_key"])
            vector_row = self.conn.execute("SELECT 1 FROM memory_vectors WHERE memory_id = ?", (row["id"],)).fetchone()
            if force_vectors or vector_row is None:
                self._write_vector(row["id"], row["summary"])
                vectors_written += 1

        vector_rows = self.conn.execute(
            "SELECT memory_id, dims, model, vector_json FROM memory_vectors ORDER BY memory_id"
        ).fetchall()
        model_counts: dict[str, int] = {}
        dims_counts: dict[str, int] = {}
        faiss_result = None
        parsed_vectors: list[VectorRow] = []
        for row in vector_rows:
            model = str(row["model"])
            dims = str(row["dims"])
            model_counts[model] = model_counts.get(model, 0) + 1
            dims_counts[dims] = dims_counts.get(dims, 0) + 1
            parsed_vectors.append(VectorRow(row["memory_id"], json.loads(row["vector_json"]), model))
        if self.settings.vector_backend in {"auto", "faiss"}:
            faiss_result = build_faiss_cache(self.settings.db_path, parsed_vectors, self.settings.embedding_model)

        metadata = {
            "fts": True,
            "vectors": True,
            "force_vectors": force_vectors,
            "vectors_written": vectors_written,
            "models": model_counts,
            "dims": dims_counts,
            "faiss": _faiss_build_result_dict(faiss_result) if faiss_result else {"status": "disabled"},
        }
        self.conn.execute(
            """
            INSERT INTO index_versions(id, index_type, model, backend, status, item_count, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _id("idx"),
                "phase1_rebuild",
                self.settings.embedding_model,
                self.settings.vector_backend,
                "completed",
                len(rows),
                _json(metadata),
                _utc_now(),
            ),
        )
        self.conn.commit()
        return {"memory_units": len(rows), **metadata}

    def _mirror_legacy_memory(
        self,
        memory_id: str,
        session_id: str,
        source_event_id: str,
        summary: str,
        tags: list[str],
        importance: float,
        created_at: str,
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO memories(id, session_id, source_event_id, summary, tags_json, importance, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, session_id, source_event_id, summary, _json(tags), importance, created_at),
        )

    def _write_vector(self, memory_id: str, summary: str) -> None:
        vector, model = embed_text_with_model(summary, self.settings.embedding_dims, self.settings.embedding_model)
        self.conn.execute(
            """
            INSERT INTO memory_vectors(memory_id, dims, model, backend, vector_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
              dims=excluded.dims,
              model=excluded.model,
              backend=excluded.backend,
              vector_json=excluded.vector_json,
              created_at=excluded.created_at
            """,
            (memory_id, len(vector), model, "sqlite", _json(vector), _utc_now()),
        )

    def _write_fts(self, memory_id: str, summary: str, subject: str, object_text: str, topic_key: str) -> None:
        try:
            self.conn.execute("DELETE FROM memory_units_fts WHERE memory_id = ?", (memory_id,))
            self.conn.execute(
                "INSERT INTO memory_units_fts(memory_id, summary, subject, object, topic_key) VALUES(?, ?, ?, ?, ?)",
                (memory_id, summary, subject, object_text, topic_key),
            )
        except sqlite3.OperationalError:
            pass

    def _write_kg_for_memory(
        self,
        memory_id: str,
        source_chunk_id: str | None,
        subject: str,
        entities: list[str],
        memory_type: str,
        confidence: float,
    ) -> None:
        subject_id = self._upsert_entity(_entity_type(subject), subject)
        memory_entity_id = self._upsert_entity("memory", memory_id)
        self._insert_edge(subject_id, memory_entity_id, "evidenced_by", memory_id, source_chunk_id, confidence)
        for entity in entities:
            entity_id = self._upsert_entity(_entity_type(entity), entity)
            self._insert_edge(subject_id, entity_id, "mentions", memory_id, source_chunk_id, confidence)
        type_entity_id = self._upsert_entity("memory_type", memory_type)
        self._insert_edge(subject_id, type_entity_id, "has_memory_type", memory_id, source_chunk_id, confidence)

    def _upsert_entity(self, entity_type: str, name: str) -> str:
        normalized = _normalize_entity(name)
        row = self.conn.execute(
            "SELECT id FROM entities WHERE entity_type = ? AND normalized_name = ?",
            (entity_type, normalized),
        ).fetchone()
        if row:
            return row["id"]
        eid = _id("ent")
        self.conn.execute(
            """
            INSERT INTO entities(id, entity_type, name, normalized_name, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (eid, entity_type, name, normalized, "{}", _utc_now()),
        )
        return eid

    def _insert_edge(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relation: str,
        evidence_memory_id: str,
        evidence_chunk_id: str | None,
        confidence: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO kg_edges(
              id, source_entity_id, target_entity_id, relation, evidence_memory_id,
              evidence_chunk_id, status, confidence, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_id("edge"), source_entity_id, target_entity_id, relation, evidence_memory_id, evidence_chunk_id, "active", confidence, _utc_now()),
        )

    def _consolidate_memory(self, memory_id: str, topic_key: str) -> None:
        new_row = self.conn.execute("SELECT * FROM memory_units WHERE id = ?", (memory_id,)).fetchone()
        if new_row is None:
            return
        candidates = self.conn.execute(
            """
            SELECT mu.*, mv.vector_json
            FROM memory_units mu
            LEFT JOIN memory_vectors mv ON mv.memory_id = mu.id
            WHERE mu.id != ? AND mu.topic_key = ? AND mu.status = 'active'
            ORDER BY mu.created_at DESC
            LIMIT 8
            """,
            (memory_id, topic_key),
        ).fetchall()
        new_vec_row = self.conn.execute(
            "SELECT vector_json FROM memory_vectors WHERE memory_id = ?",
            (memory_id,),
        ).fetchone()
        new_vec = json.loads(new_vec_row["vector_json"]) if new_vec_row else []
        best: tuple[sqlite3.Row, float, dict] | None = None
        for row in candidates:
            old_vec = json.loads(row["vector_json"]) if row["vector_json"] else []
            cosine = cosine_similarity(new_vec, old_vec) if new_vec and old_vec and len(new_vec) == len(old_vec) else 0.0
            lexical = lexical_rerank_score(new_row["summary"], row["summary"])
            entity_jaccard = _jaccard(json.loads(new_row["entities_json"]), json.loads(row["entities_json"]))
            same_topic = 1.0 if new_row["topic_key"] == row["topic_key"] else 0.0
            exact_duplicate = _memory_fingerprint(new_row) == _memory_fingerprint(row)
            contradiction = _contradiction_signal(str(new_row["object"]), str(row["object"]))
            duplicate_score = 1.0 if exact_duplicate else 0.0
            score = (
                (0.40 * cosine)
                + (0.22 * lexical)
                + (0.18 * entity_jaccard)
                + (0.10 * same_topic)
                + (0.10 * duplicate_score)
            )
            breakdown = {
                "cosine": cosine,
                "lexical": lexical,
                "entity_jaccard": entity_jaccard,
                "same_topic": same_topic,
                "exact_duplicate": duplicate_score,
                "contradiction": contradiction,
            }
            if best is None or score > best[1]:
                best = (row, score, breakdown)
        if best is None:
            self._record_consolidation(memory_id, None, "independent", 0.0, {})
            return

        related, score, breakdown = best
        if score < 0.65:
            relation = "independent"
        elif breakdown.get("contradiction"):
            relation = "contradicts"
        elif new_row["memory_type"] == related["memory_type"] and new_row["confidence"] >= related["confidence"]:
            relation = "supersedes"
        elif breakdown.get("exact_duplicate") and new_row["confidence"] < related["confidence"]:
            relation = "supports"
        else:
            relation = "refines"

        if relation == "supersedes":
            self.conn.execute("UPDATE memory_units SET status = ?, updated_at = ? WHERE id = ?", ("superseded", _utc_now(), related["id"]))
            self.conn.execute("UPDATE memory_units SET supersedes_memory_id = ?, updated_at = ? WHERE id = ?", (related["id"], _utc_now(), memory_id))
            self._mark_memory_edges_status(related["id"], "superseded")
            self._write_memory_relation_edge(memory_id, related["id"], "supersedes", new_row["source_chunk_id"], new_row["confidence"])
        elif relation == "supports" and breakdown.get("exact_duplicate"):
            self.conn.execute("UPDATE memory_units SET status = ?, supersedes_memory_id = ?, updated_at = ? WHERE id = ?", ("superseded", related["id"], _utc_now(), memory_id))
            self._mark_memory_edges_status(memory_id, "superseded")
            self._write_memory_relation_edge(memory_id, related["id"], "supports", new_row["source_chunk_id"], new_row["confidence"])
        elif relation in {"refines", "contradicts"}:
            self._write_memory_relation_edge(memory_id, related["id"], relation, new_row["source_chunk_id"], new_row["confidence"])
        self._record_consolidation(memory_id, related["id"], relation, score, breakdown)

    def _write_memory_relation_edge(
        self,
        memory_id: str,
        related_memory_id: str,
        relation: str,
        source_chunk_id: str | None,
        confidence: float,
    ) -> None:
        source_entity_id = self._upsert_entity("memory", memory_id)
        target_entity_id = self._upsert_entity("memory", related_memory_id)
        self._insert_edge(source_entity_id, target_entity_id, relation, memory_id, source_chunk_id, confidence)

    def _mark_memory_edges_status(self, memory_id: str, status: str) -> None:
        self.conn.execute(
            """
            UPDATE kg_edges
            SET status = ?
            WHERE evidence_memory_id = ?
            """,
            (status, memory_id),
        )

    def _record_consolidation(
        self,
        new_memory_id: str,
        related_memory_id: str | None,
        relation: str,
        score: float,
        breakdown: dict,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO consolidation_decisions(
              id, new_memory_id, related_memory_id, relation, score,
              score_breakdown_json, decision_status, created_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_id("cdec"), new_memory_id, related_memory_id, relation, score, _json(breakdown), "applied", _utc_now()),
        )

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


def _cleanup_reason_counts(chunks: list[Chunk]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for chunk in chunks:
        reason = str(chunk.metadata.get("amo_suppression_reason") or "")
        if reason:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _graph_node(node_id: str, label: str, node_type: str) -> dict:
    return {
        "id": node_id,
        "label": label,
        "type": node_type,
    }


def _graph_memory_fields(row: sqlite3.Row) -> dict:
    return {
        "label": _preview(row["summary"] or row["memory_id"], 80),
        "memory_id": row["memory_id"],
        "session_id": row["session_id"],
        "memory_type": row["memory_type"],
        "memory_status": row["memory_status"],
        "memory_confidence": row["memory_confidence"],
        "topic_key": row["topic_key"],
        "summary": row["summary"],
        "summary_preview": _preview(row["summary"] or "", 280),
    }


def _faiss_build_result_dict(result) -> dict:
    return {
        "backend": result.backend,
        "status": result.status,
        "item_count": result.item_count,
        "dims": result.dims,
        "model": result.model,
        "index_path": result.index_path,
        "metadata_path": result.metadata_path,
        "reason": result.reason,
    }


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def _normalize_entity(name: str) -> str:
    return re.sub(r"[^a-z0-9_.\\/-]+", "_", name.lower()).strip("_")


def _entity_type(name: str) -> str:
    if "." in name or "/" in name or "\\" in name:
        return "file"
    if name.startswith("mem_"):
        return "memory"
    return "topic"


def _jaccard(a: list[str], b: list[str]) -> float:
    left = {item.lower() for item in a}
    right = {item.lower() for item in b}
    if not left and not right:
        return 0.0
    return len(left & right) / len(left | right)


def _memory_fingerprint(row: sqlite3.Row) -> str:
    summary = str(row["summary"] or "")
    summary = re.sub(r"^(decision|fix|bug|validation|reference|observation|file change)\s*(\[[^\]]+\])?:\s*", "", summary, flags=re.I)
    text = " ".join([str(row["memory_type"] or ""), str(row["topic_key"] or ""), summary])
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _contradiction_signal(new_object: str, old_object: str) -> float:
    new_terms = _ranking_terms(new_object)
    old_terms = _ranking_terms(old_object)
    positive = {"enable", "enabled", "use", "uses", "used", "supported", "works", "pass", "passed"}
    negative = {"disable", "disabled", "not", "never", "unsupported", "fails", "failed", "error"}
    shared = new_terms & old_terms
    if len(shared) < 2:
        return 0.0
    if (new_terms & positive and old_terms & negative) or (new_terms & negative and old_terms & positive):
        return 1.0
    return 0.0


def _preview(text: str, max_len: int) -> str:
    clean = " ".join(str(text).split())
    return clean if len(clean) <= max_len else clean[: max_len - 3] + "..."
