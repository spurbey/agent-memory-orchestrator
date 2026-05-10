from __future__ import annotations

import json
import re
import sqlite3

from ..extraction import extract_tags, make_topic_key
from ..llm.embeddings import cosine_similarity, embed_text_with_model
from ..llm.vector_cache import VectorRow, build_faiss_cache
from ..core.models import Memory, MemoryUnit
from ..retrieval import lexical_rerank_score
from .common import new_id as _id
from .common import stable_json as _json
from .common import utc_now as _utc_now
from .retrieval import _ranking_terms


class MemoryStorageMixin:
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

