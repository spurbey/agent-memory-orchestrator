from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import Settings
from .db import connect, init_schema
from .embeddings import cosine_similarity, embed_text
from .models import Event, Memory


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class MemoryService:
    def __init__(self, settings: Settings, conn: sqlite3.Connection | None = None) -> None:
        self.settings = settings
        self.conn = conn or connect(settings.db_path)

    def init_db(self) -> None:
        init_schema(self.conn)

    def close(self) -> None:
        self.conn.close()

    def create_session(self, session_id: str, title: str, status: str = "draft") -> None:
        ts = _utc_now()
        self.conn.execute(
            """
            INSERT INTO sessions(id, title, status, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
              title=excluded.title,
              updated_at=excluded.updated_at
            """,
            (session_id, title, status, ts, ts),
        )
        self.conn.commit()

    def session_exists(self, session_id: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
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
    ) -> Event:
        ts = created_at or _utc_now()
        eid = event_id or _id("evt")
        payload = metadata or {}
        self.conn.execute(
            """
            INSERT INTO events(id, session_id, agent, event_type, content, metadata_json, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (eid, session_id, agent, event_type, content, json.dumps(payload), ts),
        )
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        self.conn.commit()
        return Event(
            id=eid,
            session_id=session_id,
            agent=agent,
            event_type=event_type,
            content=content,
            metadata=payload,
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
        ts = created_at or _utc_now()
        mid = memory_id or _id("mem")
        normalized_tags = tags or []
        emb = vector if vector is not None else embed_text(summary, self.settings.embedding_dims)

        self.conn.execute(
            """
            INSERT INTO memories(id, session_id, source_event_id, summary, tags_json, importance, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (mid, session_id, source_event_id, summary, json.dumps(normalized_tags), importance, ts),
        )
        self.conn.execute(
            """
            INSERT INTO memory_vectors(memory_id, dims, vector_json, created_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
              dims=excluded.dims,
              vector_json=excluded.vector_json,
              created_at=excluded.created_at
            """,
            (mid, self.settings.embedding_dims, json.dumps(emb), ts),
        )
        self.conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (ts, session_id),
        )
        self.conn.commit()
        return Memory(
            id=mid,
            session_id=session_id,
            source_event_id=source_event_id,
            summary=summary,
            tags=normalized_tags,
            importance=importance,
            created_at=ts,
        )

    def _extract_tags(self, text: str, max_tags: int = 6) -> list[str]:
        stop = {
            "this",
            "that",
            "with",
            "from",
            "have",
            "will",
            "into",
            "your",
            "about",
            "there",
            "their",
            "would",
            "should",
            "could",
            "what",
            "when",
            "where",
            "which",
        }
        words: list[str] = []
        for raw in text.lower().split():
            word = "".join(ch for ch in raw if ch.isalnum() or ch in {"-", "_"})
            if len(word) < 4 or word in stop:
                continue
            words.append(word)
        unique: list[str] = []
        seen: set[str] = set()
        for word in words:
            if word not in seen:
                seen.add(word)
                unique.append(word)
            if len(unique) >= max_tags:
                break
        return unique

    def _should_extract_memory(self, event_type: str, content: str) -> bool:
        if not content.strip():
            return False
        if event_type in {"decision", "summary", "response", "tool_result"}:
            return True
        return len(content.strip()) >= 80

    def _memory_summary(self, content: str, max_len: int = 300) -> str:
        clean = " ".join(content.split())
        return clean if len(clean) <= max_len else clean[: max_len - 3] + "..."

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
        memories_count = 0

        with file_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if not isinstance(item, dict):
                    continue

                event_type = str(item.get("event_type") or item.get("type") or "message")
                content = item.get("content") or item.get("message") or item.get("text") or ""
                if not isinstance(content, str):
                    content = json.dumps(content)
                created_at = item.get("created_at") or item.get("timestamp")
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

                event = self.add_event(
                    session_id=session_id,
                    agent=agent,
                    event_type=event_type,
                    content=content,
                    metadata=metadata,
                    created_at=created_at,
                )
                events_count += 1

                if self._should_extract_memory(event.event_type, event.content):
                    summary = self._memory_summary(event.content)
                    tags = self._extract_tags(summary)
                    importance = min(1.0, 0.3 + (len(summary) / 500.0))
                    self.add_memory(
                        session_id=session_id,
                        source_event_id=event.id,
                        summary=summary,
                        tags=tags,
                        importance=importance,
                    )
                    memories_count += 1

        return {"events": events_count, "memories": memories_count}

    def search_memories(self, query: str, session_id: str | None = None, limit: int = 10) -> list[dict]:
        params: list[object] = [f"%{query}%", f"%{query}%"]
        session_filter = ""
        if session_id:
            session_filter = "AND m.session_id = ?"
            params.append(session_id)
        params.append(max(limit * 5, limit))

        rows = self.conn.execute(
            f"""
            SELECT
              m.id,
              m.session_id,
              m.source_event_id,
              m.summary,
              m.tags_json,
              m.importance,
              m.created_at,
              mv.vector_json,
              e.content AS source_content
            FROM memories m
            JOIN events e ON e.id = m.source_event_id
            LEFT JOIN memory_vectors mv ON mv.memory_id = m.id
            WHERE (m.summary LIKE ? OR e.content LIKE ?)
            {session_filter}
            ORDER BY m.created_at DESC
            LIMIT ?
            """,
            tuple(params),
        ).fetchall()

        query_vec = embed_text(query, self.settings.embedding_dims)
        ranked: list[dict] = []
        query_lower = query.lower()

        for row in rows:
            vector_json = row["vector_json"] or "[]"
            memory_vec = json.loads(vector_json)
            lexical_hits = row["summary"].lower().count(query_lower)
            lexical_hits += row["source_content"].lower().count(query_lower)
            lexical_score = min(1.0, lexical_hits / 3.0)
            semantic_score = cosine_similarity(query_vec, memory_vec) if memory_vec else 0.0
            score = (0.45 * lexical_score) + (0.55 * semantic_score)
            ranked.append(
                {
                    "memory_id": row["id"],
                    "session_id": row["session_id"],
                    "source_event_id": row["source_event_id"],
                    "summary": row["summary"],
                    "tags": json.loads(row["tags_json"]),
                    "importance": row["importance"],
                    "created_at": row["created_at"],
                    "score": round(score, 6),
                }
            )

        ranked.sort(key=lambda item: item["score"], reverse=True)
        return ranked[:limit]

    def timeline(self, session_id: str, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """
            SELECT id, session_id, agent, event_type, content, metadata_json, created_at
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
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    def export_snapshot(self, out_path: Path, session_id: str | None = None) -> int:
        tables = [
            "sessions",
            "events",
            "memories",
            "memory_vectors",
            "orchestration_rounds",
            "orchestration_decisions",
        ]
        rows_written = 0
        out_path.parent.mkdir(parents=True, exist_ok=True)

        with out_path.open("w", encoding="utf-8") as f:
            for table in tables:
                query = f"SELECT * FROM {table}"
                params: tuple[object, ...] = ()

                if session_id:
                    if table == "sessions":
                        query = "SELECT * FROM sessions WHERE id = ?"
                        params = (session_id,)
                    elif table == "memory_vectors":
                        query = """
                        SELECT mv.*
                        FROM memory_vectors mv
                        JOIN memories m ON m.id = mv.memory_id
                        WHERE m.session_id = ?
                        """
                        params = (session_id,)
                    else:
                        query = f"SELECT * FROM {table} WHERE session_id = ?"
                        params = (session_id,)

                rows = self.conn.execute(query, params).fetchall()
                for row in rows:
                    payload = {"table": table, "row": dict(row)}
                    f.write(json.dumps(payload) + "\n")
                    rows_written += 1
        return rows_written

    def import_snapshot(self, in_path: Path) -> int:
        ordered_tables = [
            "sessions",
            "events",
            "memories",
            "memory_vectors",
            "orchestration_rounds",
            "orchestration_decisions",
        ]
        buffered: dict[str, list[dict]] = {table: [] for table in ordered_tables}

        with in_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                table = payload.get("table")
                row = payload.get("row")
                if table in buffered and isinstance(row, dict):
                    buffered[table].append(row)

        inserted = 0
        for table in ordered_tables:
            for row in buffered[table]:
                columns = list(row.keys())
                col_clause = ", ".join(columns)
                val_clause = ", ".join(["?"] * len(columns))
                values = tuple(row[col] for col in columns)
                self.conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_clause}) VALUES ({val_clause})",
                    values,
                )
                inserted += 1

        self.conn.commit()
        return inserted
