from __future__ import annotations

import json
import re
from typing import Any

from ....domain.retrieval.classification import classify_query
from ....domain.retrieval.fusion import candidate_raw_scores as _candidate_raw_scores
from ....domain.retrieval.fusion import rrf_fuse as _rrf_fuse
from ....domain.retrieval.models import RetrievalDocument
from ....domain.retrieval.models import RetrievalHit
from ....domain.retrieval.models import RetrievalResult
from ....domain.retrieval.models import TextEmbeddingProvider
from ....domain.retrieval.projection import retrieval_metadata as _retrieval_metadata
from ....domain.retrieval.ranking import rerank_document as _rerank_document
from ....infrastructure.kuzu import GraphStore
from ....infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ....llm.rerankers import RerankCandidate
from ....llm.rerankers import rerank_candidates as _default_rerank_candidates
from ....infrastructure.faiss.embedding_store import GraphEmbeddingStore
from .embedding import RETRIEVAL_EMBEDDING_KIND
from .vector import vector_candidates as _vector_candidates

rerank_candidates = _default_rerank_candidates


def retrieve_session_graph(
    *,
    query: str,
    index_store: RetrievalIndexStore,
    graph_store: GraphStore,
    embedding_store: GraphEmbeddingStore | None = None,
    embedder: TextEmbeddingProvider | None = None,
    embedding_model: str = "",
    graph_scope: str = "",
    session_id: str = "",
    repo_id: str = "",
    limit: int = 10,
    candidate_limit: int = 80,
    expand_neighbors: int = 12,
    embedding_kind: str = RETRIEVAL_EMBEDDING_KIND,
    require_vector: bool = False,
    bi_encoder_weight: float = 0.2,
    reranker_backend: str = "disabled",
    reranker_model: str = "",
    rerank_top_k: int = 50,
    rerank_max_chars: int = 1800,
    include_graph_nodes: bool = True,
) -> RetrievalResult:
    intent = classify_query(query)
    safe_repo_id = str(repo_id or "").strip()
    exact = index_store.exact_search(query, limit=candidate_limit, repo_id=safe_repo_id)
    bm25 = index_store.bm25_search(query, limit=candidate_limit, repo_id=safe_repo_id)
    vector, vector_status = _vector_candidates(
        query=query,
        index_store=index_store,
        embedding_store=embedding_store,
        embedder=embedder,
        embedding_model=embedding_model,
        graph_scope=graph_scope,
        repo_id=safe_repo_id,
        candidate_limit=candidate_limit,
        embedding_kind=embedding_kind,
    )
    if require_vector and not vector:
        raise ValueError(f"vector retrieval required but returned no candidates (status={vector_status})")
    candidate_sets = {"exact": exact, "bm25": bm25, "vector": vector}
    source_scores = _candidate_raw_scores(candidate_sets)
    fused = _rrf_fuse(candidate_sets)
    docs_by_id = index_store.get_documents_by_ids((doc_id for doc_id, _score, _sources in fused), repo_id=safe_repo_id)
    ranked: list[tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]] = []
    for doc_id, fused_score, sources in fused:
        doc = docs_by_id.get(doc_id)
        if doc is None:
            continue
        neighbors = tuple(graph_store.neighbors(doc.graph_node_id, limit=expand_neighbors)) if expand_neighbors else ()
        final_score, reasons = _rerank_document(
            query=query,
            intent=intent,
            doc=doc,
            fused_score=fused_score,
            neighbors=neighbors,
            source_scores=source_scores.get(doc_id, {}),
            bi_encoder_weight=bi_encoder_weight,
        )
        ranked.append((doc, final_score, sources, tuple(reasons), neighbors))
    ranked.sort(key=lambda item: item[1], reverse=True)
    ranked, reranker_label = _cross_encoder_rerank(
        query=query,
        intent=intent,
        ranked=ranked,
        backend=reranker_backend,
        model_name=reranker_model,
        top_k=rerank_top_k,
        max_chars=rerank_max_chars,
    )

    ranked = [item for item in ranked if _meaningful_hit(item[1], item[3])]

    graph_nodes = (
        {str(node.get("id")): node for node in graph_store.list_nodes(limit=100000, session_id=session_id)}
        if include_graph_nodes
        else {}
    )
    hits = tuple(
        RetrievalHit(
            document=doc,
            score=round(score, 6),
            sources=sources,
            reasons=reasons,
            graph_node=_compact_output_node(graph_nodes.get(doc.graph_node_id, {})),
            neighbors=tuple(_compact_output_node(node) for node in neighbors),
        )
        for doc, score, sources, reasons, neighbors in ranked[: max(1, limit)]
    )
    return RetrievalResult(
        query=query,
        intent=intent,
        hits=hits,
        vector_status=vector_status,
        reranker=reranker_label or ("deterministic+bi_encoder" if vector else "deterministic"),
        candidate_counts={
            "exact": len(exact),
            "bm25": len(bm25),
            "vector": len(vector),
            "fused": len(fused),
        },
    )


def _meaningful_hit(score: float, reasons: tuple[str, ...]) -> bool:
    if score <= 0:
        return False
    return any(
        str(reason).startswith(("term_overlap:", "topic_focus_overlap:", "central_active_boost:", "version_target_overlap:", "exact:"))
        or str(reason).startswith("bi_encoder_score:")
        for reason in reasons
    )


def _cross_encoder_rerank(
    *,
    query: str,
    intent: str,
    ranked: list[tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]],
    backend: str,
    model_name: str,
    top_k: int,
    max_chars: int,
) -> tuple[
    list[tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]],
    str,
]:
    selected_backend = str(backend or "disabled").strip().lower()
    if selected_backend in {"", "disabled", "none"} or not ranked:
        return ranked, ""
    if selected_backend not in {"auto", "lexical", "cross-encoder"}:
        raise ValueError("reranker backend must be one of: disabled, auto, lexical, cross-encoder")
    rerank_count = max(1, min(int(top_k or 50), len(ranked)))
    candidates = [
        RerankCandidate(
            memory_id=doc.doc_id,
            text=_reranker_text(doc, neighbors, max_chars=max_chars),
        )
        for doc, _score, _sources, _reasons, neighbors in ranked[:rerank_count]
    ]
    reranked = _call_rerank_candidates(
        query=query,
        candidates=candidates,
        backend=selected_backend,
        model_name=model_name,
        max_chars=max_chars,
    )
    by_doc_id = reranked.scores
    boosted: list[
        tuple[RetrievalDocument, float, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]
    ] = []
    cross_weight = _cross_encoder_weight(intent)
    for doc, score, sources, reasons, neighbors in ranked[:rerank_count]:
        cross_score = max(0.0, min(1.0, float(by_doc_id.get(doc.doc_id, 0.0))))
        new_reasons = [
            *reasons,
            f"{_safe_reranker_prefix(reranked.backend)}_score:{round(cross_score, 6)}",
            f"{_safe_reranker_prefix(reranked.backend)}_model:{reranked.model}",
            f"{_safe_reranker_prefix(reranked.backend)}_weight:{round(cross_weight, 3)}",
        ]
        if reranked.fallback_reason:
            new_reasons.append(f"reranker_fallback:{reranked.fallback_reason}")
        boosted.append((doc, score + cross_score * cross_weight, sources, tuple(new_reasons), neighbors))
    output = [*boosted, *ranked[rerank_count:]]
    output.sort(key=lambda item: item[1], reverse=True)
    base = "deterministic+bi_encoder" if any("vector" in item[2] for item in ranked) else "deterministic"
    suffix = "cross_encoder" if reranked.backend == "cross-encoder" else reranked.backend.replace("-", "_")
    if reranked.fallback_reason:
        suffix = f"{suffix}_fallback"
    return output, f"{base}+{suffix}"


def _cross_encoder_weight(intent: str) -> float:
    if intent == "decision_history":
        # Small code-oriented rerankers often over-score the literal word
        # "decision" and under-score graph-specific policy nodes such as
        # capture-only hooks. Keep them as a secondary signal for this intent.
        return 0.08
    return 0.45


def _reranker_text(
    doc: RetrievalDocument,
    neighbors: tuple[dict[str, Any], ...],
    *,
    max_chars: int,
) -> str:
    neighbor_text = "\n".join(
        f"{node.get('kind')}: {node.get('label') or ''} {node.get('summary') or ''}"
        for node in neighbors[:12]
    )
    text = "\n".join(
        part
        for part in (
            doc.title,
            doc.body,
            "metadata: " + json.dumps(doc.metadata, sort_keys=True),
            "neighbors:\n" + neighbor_text if neighbor_text else "",
        )
        if part
    )
    return text[: max(100, int(max_chars or 1800))]


def _safe_reranker_prefix(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_") or "reranker"


def _call_rerank_candidates(**kwargs: Any) -> Any:
    return rerank_candidates(**kwargs)


def _compact_output_node(node: dict[str, Any]) -> dict[str, Any]:
    if not node:
        return {}
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    compact_metadata = _retrieval_metadata(metadata)
    for key in ("path", "qualified_name", "symbol_kind", "line_start", "line_end", "hunk_ids", "version_count"):
        if key in metadata:
            compact_metadata[key] = metadata[key]
    return {
        "id": node.get("id"),
        "kind": node.get("kind"),
        "label": node.get("label"),
        "summary": _clip(str(node.get("summary") or ""), 500),
        "status": node.get("status"),
        "session_id": node.get("session_id"),
        "evidence_id": node.get("evidence_id"),
        "commit_id": node.get("commit_id"),
        "packet_id": node.get("packet_id") or metadata.get("packet_id") or metadata.get("source_packet_id"),
        "commit_sha": node.get("commit_sha") or metadata.get("commit_sha") or metadata.get("source_commit_sha"),
        "metadata": compact_metadata,
    }


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 13)].rstrip() + " ... <clipped>"




__all__ = ["RetrievalQueryService", "retrieve_session_graph"]


class RetrievalQueryService:
    """Application boundary for retrieval query execution."""

    def __init__(
        self,
        *,
        index_store: RetrievalIndexStore,
        graph_store: GraphStore,
        embedding_store: GraphEmbeddingStore | None = None,
        embedder: TextEmbeddingProvider | None = None,
    ) -> None:
        self.index_store = index_store
        self.graph_store = graph_store
        self.embedding_store = embedding_store
        self.embedder = embedder

    def retrieve(
        self,
        *,
        query: str,
        embedding_model: str = "",
        graph_scope: str = "",
        session_id: str = "",
        repo_id: str = "",
        limit: int = 10,
        candidate_limit: int = 80,
        expand_neighbors: int = 12,
        embedding_kind: str = RETRIEVAL_EMBEDDING_KIND,
        require_vector: bool = False,
        bi_encoder_weight: float = 0.2,
        reranker_backend: str = "disabled",
        reranker_model: str = "",
        rerank_top_k: int = 50,
        rerank_max_chars: int = 1800,
        include_graph_nodes: bool = True,
    ) -> RetrievalResult:
        return retrieve_session_graph(
            query=query,
            index_store=self.index_store,
            graph_store=self.graph_store,
            embedding_store=self.embedding_store,
            embedder=self.embedder,
            embedding_model=embedding_model,
            graph_scope=graph_scope,
            session_id=session_id,
            repo_id=repo_id,
            limit=limit,
            candidate_limit=candidate_limit,
            expand_neighbors=expand_neighbors,
            embedding_kind=embedding_kind,
            require_vector=require_vector,
            bi_encoder_weight=bi_encoder_weight,
            reranker_backend=reranker_backend,
            reranker_model=reranker_model,
            rerank_top_k=rerank_top_k,
            rerank_max_chars=rerank_max_chars,
            include_graph_nodes=include_graph_nodes,
        )
