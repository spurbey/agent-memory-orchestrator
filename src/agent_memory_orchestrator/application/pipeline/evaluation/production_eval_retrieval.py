from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from ....application.services.retrieval.embedding import RETRIEVAL_EMBEDDING_KIND
from ....application.services.retrieval.query import retrieve_session_graph
from ....core.config import Settings
from ....domain.retrieval.models import RetrievalCandidate
from ....domain.retrieval.models import RetrievalDocument
from ....infrastructure.faiss.embedding_store import GraphEmbeddingHit
from ....infrastructure.faiss.embedding_store import cosine_similarity
from ....infrastructure.llm.text_embedder import StrictTextEmbedder

from .production_eval_storage import _query
from .production_eval_storage import _read_json
from .production_eval_storage import _scalar


def _retrieval_state(db_path: Path, *, repo_id: str, settings: Settings) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "repo_id": repo_id, "repo_doc_count": 0, "vector_status_truthful": True}
    active_projection = _active_projection_row(db_path, repo_id=repo_id)
    active_projection_id = str(active_projection.get("projection_id") or "")
    if active_projection_id:
        doc_type_rows = _query(
            db_path,
            """
            SELECT doc_type, node_kind, COUNT(*) AS count
            FROM retrieval_documents
            WHERE repo_id = ? AND projection_id = ?
            GROUP BY doc_type, node_kind
            ORDER BY count DESC
            """,
            (repo_id, active_projection_id),
        )
    else:
        doc_type_rows = _query(
            db_path,
            """
            SELECT doc_type, node_kind, COUNT(*) AS count
            FROM retrieval_documents
            WHERE repo_id = ?
            GROUP BY doc_type, node_kind
            ORDER BY count DESC
            """,
            (repo_id,),
        )
    repo_doc_count = sum(int(row.get("count") or 0) for row in doc_type_rows)
    legacy_count = _scalar(db_path, "SELECT COUNT(*) FROM retrieval_documents WHERE COALESCE(repo_id, '') = ''")
    trace_doc_count = sum(
        int(row.get("count") or 0)
        for row in doc_type_rows
        if str(row.get("doc_type") or "") in {"session_codenode", "session_codehunk", "session_symbol", "code"}
    )
    curated_doc_count = sum(
        int(row.get("count") or 0)
        for row in doc_type_rows
        if str(row.get("doc_type") or "") in {"code_impact", "file_impact", "file_ref", "symbol_ref", "code_region_ref"}
    )
    embedded_count = _scalar(
        db_path,
        "SELECT COUNT(*) FROM graph_embeddings WHERE status = 'active' AND graph_scope = 'v2'",
        default=0,
    )
    faiss = _faiss_state(db_path)
    vector_ready = embedded_count >= repo_doc_count and repo_doc_count > 0 and str(faiss.get("status") or "") == "ready"
    query_gates = _retrieval_query_gates(db_path, repo_id=repo_id, settings=settings, require_vector=vector_ready)
    return {
        "exists": True,
        "repo_id": repo_id,
        "active_projection": active_projection,
        "active_projection_id": active_projection_id,
        "repo_doc_count": repo_doc_count,
        "legacy_doc_count": legacy_count,
        "doc_type_counts": doc_type_rows,
        "trace_doc_count": trace_doc_count,
        "curated_doc_count": curated_doc_count,
        "full_trace_dominated": trace_doc_count > max(curated_doc_count, 0),
        "strict_repo_legacy_leak": False,
        "embedding_coverage": {
            "status": "ready" if repo_doc_count and embedded_count >= repo_doc_count else "partial" if embedded_count else "missing",
            "embedded_docs": embedded_count,
            "total_docs": repo_doc_count,
        },
        "faiss": faiss,
        "query_gates": query_gates,
        "vector_status_truthful": True,
    }

def _retrieval_query_gates(db_path: Path, *, repo_id: str, settings: Settings, require_vector: bool = False) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    gates = [
        {
            "case_id": "query_control_room_uses_curated_support",
            "query": "what changed for AMO control room web UI?",
            "expected_doc_types": {"file_impact", "code_impact", "central_version", "central_atom", "reasoning", "commit", "packet"},
            "required_terms": {"amo", "control", "web"},
        },
        {
            "case_id": "query_qwen_json_uses_curated_support",
            "query": "what qwen json hardening was done?",
            "expected_doc_types": {"file_impact", "code_impact", "commit", "packet", "reasoning"},
            "required_terms": {"qwen", "json"},
        },
    ]
    index = _ReadOnlyRetrievalIndex(db_path)
    embedding_store: _ReadOnlyEmbeddingSearch | None = None
    embedder: StrictTextEmbedder | None = None
    embedding_model = ""
    graph_scope = ""
    vector_setup_error = ""
    if require_vector:
        embedding_model = str(settings.embedding_model or "").strip()
        graph_scope = _active_embedding_scope(
            db_path,
            model=embedding_model,
            preferred_scope=str(settings.retrieval_graph_scope or "").strip() or "v2",
        )
        try:
            embedder = StrictTextEmbedder(embedding_model, dims=int(settings.embedding_dims or 256))
            embedding_store = _ReadOnlyEmbeddingSearch(db_path)
        except Exception as exc:  # pragma: no cover - environment dependent
            vector_setup_error = f"{type(exc).__name__}:{exc}"
    try:
        results: list[dict[str, Any]] = []
        for gate in gates:
            query = str(gate["query"])
            result = retrieve_session_graph(
                query=query,
                index_store=index,  # type: ignore[arg-type]
                graph_store=_ReadOnlyNoGraphStore(),  # type: ignore[arg-type]
                embedding_store=embedding_store,  # type: ignore[arg-type]
                embedder=embedder,
                embedding_model=embedding_model if embedder is not None else "",
                graph_scope=graph_scope,
                repo_id=repo_id,
                limit=5,
                candidate_limit=80,
                expand_neighbors=0,
                include_graph_nodes=False,
            )
            hit_payloads = [hit.as_dict() for hit in result.hits]
            hits = [_compact_gate_hit(hit) for hit in hit_payloads]
            forbidden_hits = [
                hit
                for hit in hits
                if str(hit.get("node_kind") or "") in {"CodeNode", "CodeHunk"}
                or str(hit.get("doc_type") or "") in {"session_codenode", "session_codehunk", "code"}
            ]
            expected_support_present = any(str(hit.get("doc_type") or "") in gate["expected_doc_types"] for hit in hits)
            visible_text = "\n".join(
                f"{document.get('title')}\n{document.get('body')}"
                for hit in hit_payloads
                for document in [hit.get("document") if isinstance(hit.get("document"), dict) else {}]
            ).lower()
            required_terms = set(gate.get("required_terms") or set())
            required_terms_present = all(term in visible_text for term in required_terms)
            failures: list[str] = []
            if not hits:
                failures.append("retrieval_query_no_hits")
            if forbidden_hits:
                failures.append("retrieval_query_raw_trace_top_result")
            if not expected_support_present:
                failures.append("retrieval_query_missing_curated_support")
            if required_terms and not required_terms_present:
                failures.append("retrieval_query_missing_required_terms")
            if require_vector:
                if vector_setup_error:
                    failures.append("retrieval_query_vector_setup_failed")
                elif result.candidate_counts.get("vector", 0) <= 0:
                    failures.append("retrieval_query_no_vector_hits")
            results.append(
                {
                    "case_id": gate["case_id"],
                    "query": query,
                    "passed": not failures,
                    "hits": hits,
                    "vector_required": require_vector,
                    "vector_status": result.vector_status,
                    "vector_candidate_count": result.candidate_counts.get("vector", 0),
                    "vector_setup_error": vector_setup_error,
                    "forbidden_hits": forbidden_hits,
                    "expected_support_present": expected_support_present,
                    "required_terms": sorted(required_terms),
                    "required_terms_present": required_terms_present,
                    "blocking_failures": failures,
                    "semantic_reason": "Top production retrieval hits should be curated impact or central memory support for this query.",
                }
            )
        return results
    finally:
        if embedding_store is not None:
            embedding_store.close()
        index.close()


def _compact_gate_hit(hit: dict[str, Any]) -> dict[str, Any]:
    document = hit.get("document") if isinstance(hit.get("document"), dict) else {}
    body = str(document.get("body") or "")
    return {
        "doc_id": document.get("doc_id"),
        "doc_type": document.get("doc_type"),
        "node_kind": document.get("node_kind"),
        "repo_id": document.get("repo_id"),
        "projection_id": document.get("projection_id"),
        "packet_id": document.get("packet_id"),
        "commit_sha": document.get("commit_sha"),
        "title": document.get("title"),
        "body_excerpt": body[:240],
        "score": hit.get("score"),
        "sources": hit.get("sources"),
        "reasons": hit.get("reasons"),
    }


class _ReadOnlyNoGraphStore:
    def neighbors(self, node_id: str, *, limit: int = 25) -> list[dict[str, Any]]:
        del node_id, limit
        return []

    def list_nodes(
        self,
        *,
        limit: int = 25,
        kinds: list[str] | None = None,
        session_id: str = "",
        status: str = "",
    ) -> list[dict[str, Any]]:
        del limit, kinds, session_id, status
        return []


class _ReadOnlyRetrievalIndex:
    def __init__(self, db_path: Path) -> None:
        uri = db_path.resolve().as_posix().replace("'", "''")
        self.conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def active_projection_id(self, repo_id: str) -> str:
        try:
            rows = self.conn.execute(
                """
                SELECT projection_id
                FROM active_retrieval_projection
                WHERE repo_id = ?
                """,
                (str(repo_id or "").strip(),),
            ).fetchall()
        except sqlite3.OperationalError:
            return ""
        return str(rows[0]["projection_id"]) if rows else ""

    def list_documents(self, *, limit: int = 10000, repo_id: str = "") -> list[RetrievalDocument]:
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id and projection_id:
            rows = self.conn.execute(
                "SELECT * FROM retrieval_documents WHERE repo_id = ? AND projection_id = ? LIMIT ?",
                (safe_repo_id, projection_id, int(limit)),
            ).fetchall()
        elif safe_repo_id:
            rows = self.conn.execute(
                "SELECT * FROM retrieval_documents WHERE repo_id = ? LIMIT ?",
                (safe_repo_id, int(limit)),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM retrieval_documents LIMIT ?", (int(limit),)).fetchall()
        return [_retrieval_doc_from_row(row) for row in rows]

    def exact_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        return self._lexical_search(query, source="exact", limit=limit, repo_id=repo_id)

    def bm25_search(self, query: str, *, limit: int = 50, repo_id: str = "") -> list[RetrievalCandidate]:
        return self._lexical_search(query, source="bm25", limit=limit, repo_id=repo_id)

    def get_documents_by_ids(self, doc_ids: Any, *, repo_id: str = "") -> dict[str, RetrievalDocument]:
        ids = list(dict.fromkeys(str(doc_id) for doc_id in doc_ids if str(doc_id or "")))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        safe_repo_id = str(repo_id or "").strip()
        projection_id = self.active_projection_id(safe_repo_id) if safe_repo_id else ""
        if safe_repo_id and projection_id:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders}) AND repo_id = ? AND projection_id = ?",
                [*ids, safe_repo_id, projection_id],
            ).fetchall()
        elif safe_repo_id:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders}) AND repo_id = ?",
                [*ids, safe_repo_id],
            ).fetchall()
        else:
            rows = self.conn.execute(f"SELECT * FROM retrieval_documents WHERE doc_id IN ({placeholders})", ids).fetchall()
        return {str(row["doc_id"]): _retrieval_doc_from_row(row) for row in rows}

    def documents_by_graph_node_ids(self, node_ids: Any, *, repo_id: str = "") -> dict[str, list[RetrievalDocument]]:
        ids = list(dict.fromkeys(str(node_id) for node_id in node_ids if str(node_id or "")))
        if not ids:
            return {}
        placeholders = ",".join("?" for _ in ids)
        safe_repo_id = str(repo_id or "").strip()
        if safe_repo_id:
            rows = self.conn.execute(
                f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders}) AND repo_id = ?",
                [*ids, safe_repo_id],
            ).fetchall()
        else:
            rows = self.conn.execute(f"SELECT * FROM retrieval_documents WHERE graph_node_id IN ({placeholders})", ids).fetchall()
        out: dict[str, list[RetrievalDocument]] = {}
        for row in rows:
            doc = _retrieval_doc_from_row(row)
            out.setdefault(doc.graph_node_id, []).append(doc)
        return out

    def _lexical_search(self, query: str, *, source: str, limit: int, repo_id: str) -> list[RetrievalCandidate]:
        terms = _query_terms(query)
        if not terms:
            return []
        scored: list[tuple[float, str]] = []
        for doc in self.list_documents(limit=100000, repo_id=repo_id):
            text = f"{doc.title}\n{doc.body}".lower()
            score = sum(1.0 for term in terms if term in text)
            if score:
                scored.append((score + float(doc.importance or 0.0), doc.doc_id))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [RetrievalCandidate(doc_id=doc_id, source=source, rank=rank, raw_score=score) for rank, (score, doc_id) in enumerate(scored[:limit], start=1)]


class _ReadOnlyEmbeddingSearch:
    def __init__(self, db_path: Path) -> None:
        uri = db_path.resolve().as_posix().replace("'", "''")
        self.conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()

    def search(
        self,
        query_vector: list[float],
        *,
        embedding_kind: str,
        model: str,
        graph_scope: str = "",
        limit: int = 10,
        backend: str = "auto",
    ) -> tuple[list[GraphEmbeddingHit], str]:
        rows = self.conn.execute(
            """
            SELECT *
            FROM graph_embeddings
            WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
            """,
            (embedding_kind, model, graph_scope),
        ).fetchall()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            try:
                vector = [float(value) for value in json.loads(str(row["vector_json"] or "[]"))]
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            score = cosine_similarity(query_vector, vector)
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        hits = [
            GraphEmbeddingHit(
                embedding_id=str(row["embedding_id"]),
                node_id=str(row["node_id"]),
                node_kind=str(row["node_kind"]),
                memory_class=str(row["memory_class"]),
                graph_scope=str(row["graph_scope"]),
                graph_path=str(row["graph_path"]),
                embedding_kind=str(row["embedding_kind"]),
                model=str(row["model"]),
                score=float(score),
            )
            for score, row in scored[: max(0, int(limit))]
        ]
        return hits, "sqlite:completed" if rows else "no_embeddings"


def _active_embedding_scope(db_path: Path, *, model: str, preferred_scope: str) -> str:
    preferred = str(preferred_scope or "").strip()
    if preferred:
        count = _scalar(
            db_path,
            """
            SELECT COUNT(*)
            FROM graph_embeddings
            WHERE embedding_kind = ? AND model = ? AND graph_scope = ? AND status = 'active'
            """,
            (RETRIEVAL_EMBEDDING_KIND, model, preferred),
        )
        if count > 0:
            return preferred
    rows = _query(
        db_path,
        """
        SELECT graph_scope, COUNT(*) AS count
        FROM graph_embeddings
        WHERE embedding_kind = ? AND model = ? AND status = 'active'
        GROUP BY graph_scope
        ORDER BY count DESC, graph_scope ASC
        LIMIT 1
        """,
        (RETRIEVAL_EMBEDDING_KIND, model),
    )
    return str(rows[0].get("graph_scope") or preferred) if rows else preferred


def _query_terms(query: str) -> list[str]:
    return [term for term in re.sub(r"[^a-zA-Z0-9_.-]+", " ", query).lower().split() if len(term) > 2]


def _retrieval_doc_from_row(row: sqlite3.Row) -> RetrievalDocument:
    keys = set(row.keys())
    metadata = {}
    if "metadata_json" in keys:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            metadata = {}
    return RetrievalDocument(
        doc_id=str(row["doc_id"]),
        doc_type=str(row["doc_type"]),
        graph_node_id=str(row["graph_node_id"]),
        node_kind=str(row["node_kind"]),
        repo_id=str(row["repo_id"] if "repo_id" in keys else ""),
        projection_id=str(row["projection_id"] if "projection_id" in keys else ""),
        packet_id=str(row["packet_id"] if "packet_id" in keys else ""),
        commit_sha=str(row["commit_sha"] if "commit_sha" in keys else ""),
        title=str(row["title"]),
        body=str(row["body"]),
        chunk_index=int(row["chunk_index"] if "chunk_index" in keys else 1),
        chunk_count=int(row["chunk_count"] if "chunk_count" in keys else 1),
        memory_class=str(row["memory_class"] if "memory_class" in keys else "graph_context"),
        importance=float(row["importance"] if "importance" in keys else 0.5),
        metadata=metadata,
    )


def _faiss_state(db_path: Path) -> dict[str, Any]:
    root = db_path.parent / "indexes" / db_path.stem
    if not root.exists():
        return {"status": "missing", "item_count": 0, "path": str(root)}
    metadata_files = sorted(root.glob("*.json"))
    item_count = 0
    latest = ""
    for path in metadata_files:
        payload = _read_json(path)
        if isinstance(payload, dict):
            if isinstance(payload.get("records"), list):
                count = len(payload["records"])
            elif isinstance(payload.get("embedding_ids"), list):
                count = len(payload["embedding_ids"])
            else:
                count = 0
            if count >= item_count:
                item_count = count
                latest = str(path)
    return {"status": "ready" if item_count else "partial", "item_count": item_count, "path": latest or str(root)}


def _active_projection_row(db_path: Path, *, repo_id: str) -> dict[str, Any]:
    rows = _query(
        db_path,
        """
        SELECT retrieval_projections.*
        FROM active_retrieval_projection
        JOIN retrieval_projections ON retrieval_projections.projection_id = active_retrieval_projection.projection_id
        WHERE active_retrieval_projection.repo_id = ?
        """,
        (repo_id,),
    )
    return rows[0] if rows else {}
