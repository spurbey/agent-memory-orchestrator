from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from ..config import Settings
from ..db import connect, init_schema
from .common import elapsed_ms as _elapsed_ms
from .common import new_id as _id
from .common import stable_json as _json
from .common import utc_now as _utc_now
from .ingest import MemoryIngestMixin
from .retrieval import MemoryRetrievalMixin
from .storage import MemoryStorageMixin

class MemoryService(MemoryIngestMixin, MemoryStorageMixin, MemoryRetrievalMixin):
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


def _snake(value: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def _preview(text: str, max_len: int) -> str:
    clean = " ".join(str(text).split())
    return clean if len(clean) <= max_len else clean[: max_len - 3] + "..."
