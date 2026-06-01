from __future__ import annotations

import json
import re
import sqlite3
import time

from ..infrastructure.llm import cosine_similarity, embed_text_with_model
from ..infrastructure.llm import RerankCandidate, rerank_candidates
from ..infrastructure.llm import search_faiss_cache
from ..retrieval import build_context_pack_payload
from ..retrieval import reciprocal_rank_fusion, understand_query
from .common import elapsed_ms, new_id, stable_json, utc_now


class MemoryRetrievalMixin:

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

    def search_memories(
        self,
        query: str,
        session_id: str | None = None,
        limit: int = 10,
        *,
        include_historical: bool | None = None,
    ) -> list[dict]:
        started = time.perf_counter()
        run_id = new_id("rrun")
        understood = understand_query(query, limit)
        historical = understood.include_historical if include_historical is None else include_historical
        ts = utc_now()
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
                stable_json(
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
                ("completed", utc_now(), elapsed_ms(started), run_id),
            )
            self.conn.commit()
            return results
        except Exception:
            self.conn.execute(
                "UPDATE retrieval_runs SET status = ?, finished_at = ?, duration_ms = ? WHERE id = ?",
                ("failed", utc_now(), elapsed_ms(started), run_id),
            )
            self.conn.commit()
            raise

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
        if self.settings.vector_backend == "disabled":
            return []
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
                    new_id("rcand"),
                    run_id,
                    item["memory_id"],
                    ",".join(sorted(item["source_ranks"].keys())),
                    rank,
                    max([float(v) for v in item["raw_scores"].values()] or [0.0]),
                    item["rrf_score"],
                    item["rerank_score"],
                    item["score"],
                    stable_json(
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
                    utc_now(),
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
        self.conn.execute("UPDATE retrieval_runs SET config_json = ? WHERE id = ?", (stable_json(config), run_id))


def _fts_query(query: str) -> str:
    terms = re.findall(r"[A-Za-z0-9_./-]+", query)
    safe = [term.replace('"', "") for term in terms if len(term) >= 2]
    return " OR ".join(f'"{term}"' for term in safe) or '""'

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
