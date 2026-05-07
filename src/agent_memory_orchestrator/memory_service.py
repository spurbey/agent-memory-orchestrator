from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .adapters import infer_codex_session, normalize_adapter_event
from .chunker import chunk_text
from .cleaning import clean_event_text
from .config import Settings
from .context_pack import build_context_pack_payload
from .db import connect, init_schema
from .embeddings import cosine_similarity, embed_text_with_model
from .extraction import extract_memory_candidates, extract_tags, make_topic_key
from .models import Chunk, Event, Memory, MemoryUnit
from .privacy import redact_secrets
from .rerankers import RerankCandidate, rerank_candidates
from .retrieval import lexical_rerank_score, reciprocal_rank_fusion, understand_query
from .vector_cache import VectorRow, build_faiss_cache, search_faiss_cache

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


class MemoryService:
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

    def ingest_hook_payload(self, payload: dict, default_agent: str = "codex") -> dict[str, object]:
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
            process=True,
        )
        if event.event_type.lower() in {"stop", "session_stop"}:
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

    def build_context_pack(
        self,
        query: str,
        session_id: str | None = None,
        budget_tokens: int | None = None,
        limit: int = 12,
        *,
        include_historical: bool = False,
    ) -> dict:
        results = self.search_memories(
            query,
            session_id=session_id,
            limit=max(limit, 1),
            include_historical=include_historical,
        )
        retrieval_run_id = results[0].get("retrieval_run_id") if results else None
        pack = build_context_pack_payload(
            query=query,
            results=results,
            budget_tokens=budget_tokens or self.settings.context_budget,
            retrieval_run_id=retrieval_run_id,
            include_historical=include_historical,
        )
        return {"text": pack.text, **pack.payload}

    def codex_hook_response(self, payload: dict, default_agent: str = "codex") -> dict:
        event_name = str(payload.get("hook_event_name") or payload.get("event_type") or "")
        normalized_name = _snake(event_name)
        additional_context = self.build_hook_context(payload, default_agent=default_agent)
        self.ingest_hook_payload(payload, default_agent=default_agent)

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

    def search_memories(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 10,
        *,
        include_historical: bool | None = None,
    ) -> list[dict]:
        started = time.perf_counter()
        run_id = _id("rrun")
        understood = understand_query(query, limit)
        historical = understood.include_historical if include_historical is None else include_historical
        ts = _utc_now()
        self.conn.execute(
            """
            INSERT INTO retrieval_runs(
              id, query, intent, session_id, include_historical, status,
              started_at, config_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                query,
                understood.intent,
                session_id,
                1 if historical else 0,
                "running",
                ts,
                _json(
                    {
                        "pools": understood.pools,
                        "entities": understood.entities,
                        "reranker": {
                            "backend": self.settings.reranker_backend,
                            "model": self.settings.reranker_model,
                            "top_k": self.settings.rerank_top_k,
                            "max_chars": self.settings.rerank_max_chars,
                        },
                        "vector_backend": self.settings.vector_backend,
                    }
                ),
            ),
        )
        self.conn.commit()

        try:
            bm25 = self._bm25_candidates(query, session_id, understood.pools["bm25"], historical)
            vector = self._vector_candidates(query, session_id, understood.pools["vector"], historical)
            kg = self._kg_candidates(query, session_id, understood.pools["kg"], historical)
            fused = reciprocal_rank_fusion({"bm25": bm25, "vector": vector, "kg": kg})
            results = self._materialize_results(query, fused, run_id, limit, historical)
            self.conn.execute(
                """
                UPDATE retrieval_runs
                SET status = ?, finished_at = ?, duration_ms = ?
                WHERE id = ?
                """,
                ("completed", _utc_now(), _elapsed_ms(started), run_id),
            )
            self.conn.commit()
            return results
        except Exception:
            self.conn.execute(
                "UPDATE retrieval_runs SET status = ?, finished_at = ?, duration_ms = ? WHERE id = ?",
                ("failed", _utc_now(), _elapsed_ms(started), run_id),
            )
            self.conn.commit()
            raise

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
              COUNT(DISTINCT e.id) AS event_count,
              COUNT(DISTINCT mu.id) AS memory_count,
              ss.summary_text AS summary_text
            FROM sessions s
            LEFT JOIN events e ON e.session_id = s.id
            LEFT JOIN memory_units mu ON mu.session_id = s.id
            LEFT JOIN session_summaries ss ON ss.session_id = s.id
            GROUP BY s.id
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

    def _bm25_candidates(self, query: str, session_id: str | None, limit: int, include_historical: bool) -> list[tuple[str, float]]:
        status_clause = "" if include_historical else "AND mu.status = 'active'"
        session_clause = "AND mu.session_id = ?" if session_id else ""
        params: list[object] = []
        match = _fts_query(query)
        try:
            sql = f"""
            SELECT mu.id, bm25(memory_units_fts) * -1 AS score
            FROM memory_units_fts
            JOIN memory_units mu ON mu.id = memory_units_fts.memory_id
            WHERE memory_units_fts MATCH ?
            {session_clause}
            {status_clause}
            ORDER BY bm25(memory_units_fts)
            LIMIT ?
            """
            params = [match]
            if session_id:
                params.append(session_id)
            params.append(limit)
            rows = self.conn.execute(sql, tuple(params)).fetchall()
            return [(row["id"], float(row["score"])) for row in rows]
        except sqlite3.OperationalError:
            like = f"%{query}%"
            sql = f"""
            SELECT id, 1.0 AS score
            FROM memory_units mu
            WHERE (summary LIKE ? OR subject LIKE ? OR object LIKE ? OR topic_key LIKE ?)
            {session_clause}
            {status_clause}
            ORDER BY created_at DESC
            LIMIT ?
            """
            params = [like, like, like, like]
            if session_id:
                params.append(session_id)
            params.append(limit)
            rows = self.conn.execute(sql, tuple(params)).fetchall()
            return [(row["id"], float(row["score"])) for row in rows]

    def _vector_candidates(self, query: str, session_id: str | None, limit: int, include_historical: bool) -> list[tuple[str, float]]:
        query_vec, _ = embed_text_with_model(query, self.settings.embedding_dims, self.settings.embedding_model)
        if self.settings.vector_backend in {"auto", "faiss"}:
            faiss = search_faiss_cache(self.settings.db_path, query_vec, max(limit * 5, limit))
            if faiss.status == "completed":
                filtered = self._filter_vector_candidates(faiss.candidates, session_id, include_historical)
                if filtered:
                    return filtered[:limit]
        status_clause = "" if include_historical else "AND mu.status = 'active'"
        session_clause = "AND mu.session_id = ?" if session_id else ""
        rows = self.conn.execute(
            f"""
            SELECT mu.id, mu.summary, mv.vector_json
            FROM memory_units mu
            JOIN memory_vectors mv ON mv.memory_id = mu.id
            WHERE 1 = 1
            {session_clause}
            {status_clause}
            """,
            (session_id,) if session_id else (),
        ).fetchall()
        scored = []
        for row in rows:
            vec = json.loads(row["vector_json"])
            if len(vec) != len(query_vec):
                continue
            scored.append((row["id"], cosine_similarity(query_vec, vec)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    def _filter_vector_candidates(
        self,
        candidates: list[tuple[str, float]],
        session_id: str | None,
        include_historical: bool,
    ) -> list[tuple[str, float]]:
        if not candidates:
            return []
        allowed: list[tuple[str, float]] = []
        for memory_id, score in candidates:
            row = self.conn.execute(
                "SELECT session_id, status FROM memory_units WHERE id = ?",
                (memory_id,),
            ).fetchone()
            if row is None:
                continue
            if session_id and row["session_id"] != session_id:
                continue
            if not include_historical and row["status"] != "active":
                continue
            allowed.append((memory_id, score))
        return allowed

    def _kg_candidates(self, query: str, session_id: str | None, limit: int, include_historical: bool) -> list[tuple[str, float]]:
        terms = [term for term in re.findall(r"[a-z0-9_.\\/-]+", query.lower()) if len(term) >= 3][:8]
        if not terms:
            return []
        status_clause = "" if include_historical else "AND mu.status = 'active'"
        session_clause = "AND mu.session_id = ?" if session_id else ""
        scores: dict[str, float] = {}
        for term in terms:
            like = f"%{term}%"
            params: list[object] = [like, like]
            if session_id:
                params.append(session_id)
            params.append(limit)
            rows = self.conn.execute(
                f"""
                SELECT DISTINCT mu.id, ke.confidence
                FROM entities e
                JOIN kg_edges ke ON ke.source_entity_id = e.id OR ke.target_entity_id = e.id
                JOIN memory_units mu ON mu.id = ke.evidence_memory_id
                WHERE (e.normalized_name LIKE ? OR e.name LIKE ?)
                {session_clause}
                {status_clause}
                ORDER BY ke.confidence DESC
                LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            for row in rows:
                scores[row["id"]] = max(scores.get(row["id"], 0.0), float(row["confidence"]))
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return ranked[:limit]

    def _materialize_results(
        self,
        query: str,
        fused: dict[str, dict],
        run_id: str,
        limit: int,
        include_historical: bool,
    ) -> list[dict]:
        materialized: list[dict] = []
        ranked_fused = sorted(fused.items(), key=lambda item: float(item[1]["rrf_score"]), reverse=True)
        ranked_fused = ranked_fused[: max(self.settings.rerank_top_k, limit)]
        candidates: list[tuple[str, dict, sqlite3.Row, list[str], list[str]]] = []
        for memory_id, fused_item in ranked_fused:
            row = self.conn.execute(
                """
                SELECT mu.*, e.content AS source_content
                FROM memory_units mu
                JOIN events e ON e.id = mu.source_event_id
                WHERE mu.id = ?
                """,
                (memory_id,),
            ).fetchone()
            if row is None:
                continue
            entities = json.loads(row["entities_json"])
            tags = json.loads(row["tags_json"])
            candidates.append((memory_id, fused_item, row, entities, tags))

        rerank_inputs = [
            RerankCandidate(
                memory_id=memory_id,
                text=f"{row['summary']} {row['subject']} {row['object']}",
            )
            for memory_id, _fused_item, row, _entities, _tags in candidates
        ]
        rerank = rerank_candidates(
            query=query,
            candidates=rerank_inputs,
            backend=self.settings.reranker_backend,
            model_name=self.settings.reranker_model,
            max_chars=self.settings.rerank_max_chars,
        )
        self._update_retrieval_run_config(
            run_id,
            {
                "actual_reranker": {
                    "backend": rerank.backend,
                    "model": rerank.model,
                    "fallback_reason": rerank.fallback_reason,
                }
            },
        )

        for memory_id, fused_item, row, entities, tags in candidates:
            rerank_score = rerank.scores.get(memory_id, 0.0)
            policy = _ranking_policy(
                query=query,
                row=row,
                entities=entities,
                tags=tags,
                rrf_score=float(fused_item["rrf_score"]),
                rerank_score=rerank_score,
                include_historical=include_historical,
            )
            final_score = policy["final_score"]
            if final_score <= 0.01:
                continue
            item = {
                "memory_id": row["id"],
                "session_id": row["session_id"],
                "source_event_id": row["source_event_id"],
                "source_chunk_id": row["source_chunk_id"],
                "memory_type": row["memory_type"],
                "summary": row["summary"],
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "topic_key": row["topic_key"],
                "entities": entities,
                "tags": tags,
                "confidence": row["confidence"],
                "importance": row["importance"],
                "status": row["status"],
                "created_at": row["created_at"],
                "retrieval_run_id": run_id,
                "score": round(final_score, 6),
                "rrf_score": round(float(fused_item["rrf_score"]), 6),
                "rerank_score": round(rerank_score, 6),
                "reranker_backend": rerank.backend,
                "reranker_model": rerank.model,
                "reranker_fallback_reason": rerank.fallback_reason,
                "source_ranks": fused_item["sources"],
                "raw_scores": fused_item["raw_scores"],
                "ranking_policy": {key: value for key, value in policy.items() if key != "final_score"},
            }
            materialized.append(item)

        materialized.sort(key=lambda item: item["score"], reverse=True)
        for rank, item in enumerate(materialized, start=1):
            self.conn.execute(
                """
                INSERT INTO retrieval_candidates(
                  id, retrieval_run_id, memory_id, source, rank, raw_score,
                  rrf_score, rerank_score, final_score, score_breakdown_json, created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _id("rcand"),
                    run_id,
                    item["memory_id"],
                    ",".join(sorted(item["source_ranks"].keys())),
                    rank,
                    max([float(v) for v in item["raw_scores"].values()] or [0.0]),
                    item["rrf_score"],
                    item["rerank_score"],
                    item["score"],
                    _json(
                        {
                            "source_ranks": item["source_ranks"],
                            "raw_scores": item["raw_scores"],
                            "ranking_policy": item["ranking_policy"],
                            "reranker": {
                                "backend": item["reranker_backend"],
                                "model": item["reranker_model"],
                                "fallback_reason": item["reranker_fallback_reason"],
                            },
                        }
                    ),
                    _utc_now(),
                ),
            )
        return materialized[:limit]

    def _update_retrieval_run_config(self, run_id: str, extra: dict) -> None:
        row = self.conn.execute("SELECT config_json FROM retrieval_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return
        try:
            config = json.loads(row["config_json"])
        except Exception:
            config = {}
        config.update(extra)
        self.conn.execute("UPDATE retrieval_runs SET config_json = ? WHERE id = ?", (_json(config), run_id))

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


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_./-]+", query)
    safe = [term.replace('"', "") for term in terms if len(term) >= 2]
    return " OR ".join(f'"{term}"' for term in safe) or '""'


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


def _ranking_policy(
    *,
    query: str,
    row: sqlite3.Row,
    entities: list[str],
    tags: list[str],
    rrf_score: float,
    rerank_score: float,
    include_historical: bool,
) -> dict[str, object]:
    query_terms = _ranking_terms(query)
    entity_terms = _ranking_terms(" ".join([row["subject"], row["topic_key"], *entities]))
    tag_terms = _ranking_terms(" ".join(tags))
    body_terms = _ranking_terms(f"{row['summary']} {row['object']}")
    candidate_terms = entity_terms | tag_terms | body_terms
    matched_terms = sorted(query_terms & candidate_terms)

    confidence = _clamp(float(row["confidence"] or 0.0), 0.0, 1.0)
    importance = _clamp(float(row["importance"] or 0.0), 0.0, 1.0)
    memory_type = str(row["memory_type"] or "").lower()

    base_rerank = 0.50 * rerank_score
    base_rrf = 0.20 * rrf_score
    exact_boost = _exact_match_boost(query_terms, entity_terms, tag_terms, body_terms)
    has_query_evidence = rerank_score > 0.0 or exact_boost > 0.0
    confidence_boost = 0.16 * confidence if has_query_evidence else 0.0
    importance_boost = 0.06 * importance if has_query_evidence else 0.0
    type_boost = _memory_type_boost(query_terms, memory_type) if has_query_evidence else 0.0
    quality_boost = _quality_boost(query_terms, row) if has_query_evidence else 0.0
    noise_penalty = _noise_penalty(row, confidence, query_terms)
    historical_penalty = 0.0 if include_historical or row["status"] == "active" else -0.20

    final_score = (
        base_rerank
        + base_rrf
        + confidence_boost
        + importance_boost
        + type_boost
        + quality_boost
        + exact_boost
        + historical_penalty
        - noise_penalty
    )
    return {
        "final_score": round(final_score, 6),
        "base_rerank": round(base_rerank, 6),
        "base_rrf": round(base_rrf, 6),
        "confidence_boost": round(confidence_boost, 6),
        "importance_boost": round(importance_boost, 6),
        "type_boost": round(type_boost, 6),
        "quality_boost": round(quality_boost, 6),
        "exact_boost": round(exact_boost, 6),
        "noise_penalty": round(noise_penalty, 6),
        "historical_penalty": round(historical_penalty, 6),
        "matched_terms": matched_terms[:12],
    }


_RANKING_STOPWORDS = {
    "about",
    "agent",
    "after",
    "before",
    "does",
    "did",
    "from",
    "have",
    "into",
    "memory",
    "that",
    "their",
    "there",
    "this",
    "what",
    "were",
    "when",
    "where",
    "which",
    "with",
    "would",
    "you",
}


_DECISION_TERMS = {
    "agree",
    "agreed",
    "approve",
    "approved",
    "choose",
    "chosen",
    "decide",
    "decided",
    "decision",
    "final",
    "finalize",
    "finalized",
    "settled",
}


_CAUSAL_TERMS = {"cause", "caused", "reason", "rationale", "why"}


_RETRIEVAL_TERMS = {
    "bm25",
    "candidate",
    "candidates",
    "cross",
    "encoder",
    "faiss",
    "fusion",
    "kg",
    "rank",
    "ranking",
    "rerank",
    "reranker",
    "reranking",
    "retrieval",
    "rrf",
    "score",
    "vector",
}


_HOOK_TERMS = {
    "codex_hooks",
    "hook",
    "hooks",
    "permissionrequest",
    "posttooluse",
    "pretooluse",
    "sessionstart",
    "stop",
    "userpromptsubmit",
}


def _ranking_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw in re.findall(r"[a-z0-9_./\\-]+", str(text).lower()):
        for term in re.findall(r"[a-z0-9_]+", raw):
            if len(term) < 3 or term in _RANKING_STOPWORDS:
                continue
            terms.add(term)
            if term.endswith("s") and len(term) > 4:
                terms.add(term[:-1])
            if term.endswith("ing") and len(term) > 6:
                terms.add(term[:-3])
    return terms


def _memory_type_boost(query_terms: set[str], memory_type: str) -> float:
    asks_decision = bool(query_terms & _DECISION_TERMS)
    asks_causal = bool(query_terms & _CAUSAL_TERMS)
    if asks_decision:
        if memory_type in {"meta", "test_artifact"}:
            return -0.28
        if memory_type == "decision":
            return 0.14
        if memory_type in {"fix", "summary", "validation"}:
            return 0.10
        if memory_type in {"blocker", "bug"}:
            return 0.04
        if memory_type == "observation":
            return -0.16
    if asks_causal:
        if memory_type in {"decision", "fix", "bug"}:
            return 0.08
        if memory_type == "observation":
            return -0.03
    return 0.0


def _quality_boost(query_terms: set[str], row: sqlite3.Row) -> float:
    summary = str(row["summary"] or "").lower()
    memory_type = str(row["memory_type"] or "").lower()
    boost = 0.0
    if query_terms & _DECISION_TERMS:
        if any(marker in summary for marker in ("final decision", "we decided", "decided to", "actual architecture")):
            boost += 0.12
        if any(marker in summary for marker in ("implemented now", "official codex docs", "supported behind this feature flag")):
            boost += 0.08
    if query_terms & {"hook", "hooks", "codex_hooks"}:
        hook_markers = {"codex_hooks", "userpromptsubmit", "sessionstart", "posttooluse", "pretooluse", "stop"}
        matched = sum(1 for marker in hook_markers if marker in summary)
        boost += min(0.16, 0.04 * matched)
        if ".codex/config.toml" in summary:
            boost += 0.05
    if memory_type in {"meta", "test_artifact"}:
        boost -= 0.10
    return round(max(-0.20, min(0.28, boost)), 6)


def _exact_match_boost(
    query_terms: set[str],
    entity_terms: set[str],
    tag_terms: set[str],
    body_terms: set[str],
) -> float:
    entity_boost = min(0.09, 0.035 * len(query_terms & entity_terms))
    tag_boost = min(0.04, 0.010 * len(query_terms & tag_terms))
    body_boost = min(0.04, 0.010 * len(query_terms & body_terms))
    hook_boost = 0.0
    if query_terms & {"hook", "hooks"} and (entity_terms | tag_terms | body_terms) & _HOOK_TERMS:
        hook_boost = 0.08
    return entity_boost + tag_boost + body_boost + hook_boost


def _noise_penalty(row: sqlite3.Row, confidence: float, query_terms: set[str]) -> float:
    summary = str(row["summary"] or "").lower()
    subject = str(row["subject"] or "").lower()
    memory_type = str(row["memory_type"] or "").lower()
    penalty = 0.0
    retrieval_query = bool(query_terms & _RETRIEVAL_TERMS)
    if memory_type == "observation" and confidence <= 0.45:
        penalty += 0.06
    if memory_type == "meta" and not retrieval_query:
        penalty += 0.35
    if memory_type == "test_artifact":
        penalty += 0.45
    if subject in {"change", "changes", "command", "context", "output", "session"}:
        penalty += 0.04
    if any(marker in summary for marker in ("context from my ide setup", "open tabs:", "active file:")):
        penalty += 0.18
    if subject in {"untitled9.md", "temp_result.txt"} and memory_type in {"decision", "observation"}:
        penalty += 0.08
    if any(marker in summary for marker in ("command completed:", "powershell.exe", "rg \\\"^# cell")):
        penalty += 0.10
    if '"call_id"' in summary and '"invocation"' in summary and '"result"' in summary:
        penalty += 0.18
    if not retrieval_query and _looks_like_retrieval_meta_noise(summary):
        penalty += 0.45
    if _looks_like_test_artifact_noise(summary):
        penalty += 0.50
    if memory_type == "observation" and any(marker in summary for marker in ("that result means", "this output means")):
        penalty += 0.08
    return min(0.85, penalty)


def _looks_like_retrieval_meta_noise(summary: str) -> bool:
    markers = (
        "cross-encoder",
        "reranking",
        "reranker",
        "bm25",
        "vector search",
        "kg finds",
        "candidate a",
        "candidate b",
        "for query:",
        "query: what did we decide",
        "ranking policy",
        "what is now fixed",
        "correct memory is now ranked",
        "context pack",
        "raw mcp/tool json",
    )
    return sum(1 for marker in markers if marker in summary) >= 2


def _looks_like_test_artifact_noise(summary: str) -> bool:
    markers = (
        "source_event_id=",
        "source_chunk_id=",
        "memory_type=",
        "object_text=",
        "assert ",
        "tmp_path",
        "svc.add_event",
        "make_settings(",
        "rollout-test",
        "pytest",
    )
    return sum(1 for marker in markers if marker in summary) >= 2


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _preview(text: str, max_len: int) -> str:
    clean = " ".join(str(text).split())
    return clean if len(clean) <= max_len else clean[: max_len - 3] + "..."
