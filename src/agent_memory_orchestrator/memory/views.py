from __future__ import annotations

import json
import sqlite3

from .common import preview as _preview


class MemoryViewsMixin:
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
