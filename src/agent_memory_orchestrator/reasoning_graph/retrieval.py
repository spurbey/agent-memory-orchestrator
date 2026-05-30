from __future__ import annotations

import json
import re
from typing import Any

from ..domain.retrieval.classification import classify_query
from ..domain.retrieval.classification import query_has_code_locator as _query_has_code_locator
from ..domain.retrieval.fusion import candidate_raw_scores as _candidate_raw_scores
from ..domain.retrieval.fusion import rrf_fuse as _rrf_fuse
from ..domain.retrieval.models import EmbeddingRunResult
from ..domain.retrieval.models import RetrievalCandidate
from ..domain.retrieval.models import RetrievalDocument
from ..domain.retrieval.models import RetrievalHit
from ..domain.retrieval.models import RetrievalResult
from ..domain.retrieval.models import TextEmbeddingProvider
from ..domain.retrieval.projection import CENTRAL_RETRIEVAL_NODE_KINDS as CENTRAL_RETRIEVAL_NODE_KINDS
from ..domain.retrieval.projection import DEFAULT_RETRIEVAL_NODE_KINDS as DEFAULT_RETRIEVAL_NODE_KINDS
from ..domain.retrieval.projection import SESSION_RETRIEVAL_NODE_KINDS as SESSION_RETRIEVAL_NODE_KINDS
from ..domain.retrieval.projection import build_retrieval_documents_from_graph as build_retrieval_documents_from_graph
from ..domain.retrieval.projection import retrieval_metadata as _retrieval_metadata
from ..domain.retrieval.text import QUERY_STOPWORDS
from ..domain.retrieval.text import expanded_query_terms as _expanded_query_terms
from ..domain.retrieval.text import normalize as _normalize
from ..domain.retrieval.text import stem_term as _stem_term
from ..domain.retrieval.text import terms as _terms
from ..graph.store import GraphStore
from ..infrastructure.sqlite.retrieval_store import RetrievalIndexStore
from ..llm.rerankers import RerankCandidate
from ..llm.rerankers import rerank_candidates
from .embedding_store import GraphEmbeddingHit
from .embedding_store import GraphEmbeddingRecord
from .embedding_store import GraphEmbeddingStore
from .embedding_store import hash_content


RETRIEVAL_EMBEDDING_KIND = "retrieval_text"
VERSION_FLOW_OPERATOR_TERMS = {
    "flow",
    "history",
    "show",
    "symbol",
    "version",
    "versions",
}

DECISION_HISTORY_OPERATOR_TERMS = {
    "decision",
    "decide",
    "decided",
    "made",
}

CODE_WHY_OPERATOR_TERMS = {
    "change",
    "changed",
    "code",
    "file",
}

AGENT_CONTEXT_TERMS = {
    "agent",
    "claude",
    "codex",
}


def embed_missing_retrieval_documents(
    *,
    index_store: RetrievalIndexStore,
    embedding_store: GraphEmbeddingStore,
    embedder: TextEmbeddingProvider,
    model: str,
    graph_scope: str,
    session_id: str = "",
    repo_id: str = "",
    extraction_run_id: str = "",
    limit: int = 0,
    embedding_kind: str = RETRIEVAL_EMBEDDING_KIND,
) -> EmbeddingRunResult:
    docs = index_store.list_documents(limit=100000, repo_id=repo_id)
    existing = embedding_store.list_records(
        embedding_kind=embedding_kind,
        model=model,
        graph_scope=graph_scope,
        status="active",
        limit=100000,
    )
    existing_hashes = {(record.graph_path, record.content_hash) for record in existing}
    embedded = 0
    already = 0
    skipped_empty = 0
    dims = 0
    limit_hit = False
    for doc in docs:
        text = doc.embedding_text()
        if not text.strip():
            skipped_empty += 1
            continue
        content_hash = hash_content(text)
        if (doc.doc_id, content_hash) in existing_hashes:
            already += 1
            continue
        if limit and embedded >= limit:
            limit_hit = True
            break
        vector = [float(value) for value in embedder.embed(text)]
        if not vector:
            skipped_empty += 1
            continue
        dims = dims or len(vector)
        record = GraphEmbeddingRecord.create(
            node_id=doc.graph_node_id,
            node_kind=doc.node_kind,
            memory_class=doc.memory_class,
            graph_scope=graph_scope,
            graph_path=doc.doc_id,
            session_id=session_id,
            extraction_run_id=extraction_run_id,
            embedding_kind=embedding_kind,
            model=model,
            text=text,
            vector=vector,
            importance=doc.importance,
            memory_tier="hot",
            status="active",
        )
        embedding_store.mark_stale_for_graph_path(
            graph_path=doc.doc_id,
            embedding_kind=embedding_kind,
            model=model,
            graph_scope=graph_scope,
            keep_content_hash=record.content_hash,
        )
        embedding_store.upsert(record)
        existing_hashes.add((doc.doc_id, content_hash))
        embedded += 1
    return EmbeddingRunResult(
        total_docs=len(docs),
        already_embedded=already,
        embedded=embedded,
        skipped_empty=skipped_empty,
        model=model,
        dims=dims,
        limit_hit=limit_hit,
    )


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
    reranked = rerank_candidates(
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


def _vector_candidates(
    *,
    query: str,
    index_store: RetrievalIndexStore,
    embedding_store: GraphEmbeddingStore | None,
    embedder: TextEmbeddingProvider | None,
    embedding_model: str,
    graph_scope: str,
    candidate_limit: int,
    embedding_kind: str,
    repo_id: str = "",
) -> tuple[list[RetrievalCandidate], str]:
    if embedding_store is None or embedder is None or not embedding_model:
        return [], "not_requested"
    query_vector = embedder.embed(query)
    hits, status = embedding_store.search(
        query_vector,
        embedding_kind=embedding_kind,
        model=embedding_model,
        graph_scope=graph_scope,
        limit=candidate_limit,
    )
    doc_ids: list[str] = []
    scores: dict[str, float] = {}
    hits_without_graph_path: list[GraphEmbeddingHit] = []
    for hit in hits:
        if hit.graph_path:
            doc_ids.append(hit.graph_path)
            scores[hit.graph_path] = max(scores.get(hit.graph_path, 0.0), hit.score)
        else:
            hits_without_graph_path.append(hit)
    if hits_without_graph_path:
        docs_by_node = index_store.documents_by_graph_node_ids(
            (hit.node_id for hit in hits_without_graph_path),
            repo_id=repo_id,
        )
        for hit in hits_without_graph_path:
            for doc in docs_by_node.get(hit.node_id, [])[:1]:
                doc_ids.append(doc.doc_id)
                scores[doc.doc_id] = max(scores.get(doc.doc_id, 0.0), hit.score)
    if repo_id:
        docs_by_id = index_store.get_documents_by_ids(doc_ids, repo_id=repo_id)
        doc_ids = [doc_id for doc_id in doc_ids if doc_id in docs_by_id]
    return (
        [
            RetrievalCandidate(doc_id, "vector", rank, scores.get(doc_id, 0.0))
            for rank, doc_id in enumerate(dict.fromkeys(doc_ids), start=1)
        ],
        status,
    )


def _rerank_document(
    *,
    query: str,
    intent: str,
    doc: RetrievalDocument,
    fused_score: float,
    neighbors: tuple[dict[str, Any], ...],
    source_scores: dict[str, float],
    bi_encoder_weight: float,
) -> tuple[float, list[str]]:
    terms = _terms(query)
    scoring_terms = terms
    if intent == "version_flow":
        # "version flow" is an operator phrase. Rank by the requested symbol/path
        # terms, otherwise functions named "version_flow" beat the actual symbol.
        scoring_terms = terms.difference(VERSION_FLOW_OPERATOR_TERMS) or terms
    text = _normalize(f"{doc.title} {doc.body} {json.dumps(doc.metadata, sort_keys=True)}")
    primary_text = _primary_rank_text(doc, include_code_locator_context=_query_has_code_locator(query))
    reasons = [f"fused:{round(fused_score, 6)}"]
    score = fused_score
    overlap = [term for term in scoring_terms if term in text]
    if overlap:
        overlap_ratio = len(overlap) / max(1, len(scoring_terms))
        score += min(0.4, overlap_ratio * 0.4)
        reasons.append("term_overlap:" + ",".join(overlap[:8]))
    topic_terms = _topic_terms(query, intent)
    topic_overlap_ratio = 0.0
    if topic_terms:
        topic_overlap = [term for term in topic_terms if term in primary_text]
        if topic_overlap:
            topic_overlap_ratio = len(topic_overlap) / max(1, len(topic_terms))
            score += min(0.5, topic_overlap_ratio * 0.5)
            reasons.append("topic_focus_overlap:" + ",".join(topic_overlap[:8]))
        elif intent in {"code_why", "decision_history"}:
            score -= 0.18
            reasons.append("topic_focus_penalty")
    if doc.doc_type == "central_version":
        central_boost = _central_version_boost(doc, intent=intent, query=query, topic_overlap_ratio=topic_overlap_ratio)
        score += central_boost
        if central_boost:
            reasons.append(f"central_active_boost:{round(central_boost, 3)}")
        else:
            score -= 0.05
            reasons.append("central_low_topic_overlap_penalty")
        if intent == "version_flow" and _query_has_code_locator(query) and not _code_locator_match(query, text):
            score -= 0.25
            reasons.append("code_locator_mismatch_penalty")
    elif doc.doc_type == "central_atom":
        if topic_overlap_ratio >= 0.4 or intent == "version_flow" or _query_has_code_locator(query):
            score += 0.10
            reasons.append("central_atom_context_boost")
    elif doc.doc_type == "graph_lineage" and intent not in {"version_flow"}:
        score -= 0.12
        reasons.append("graph_lineage_penalty")
    if intent in {"code_why", "decision_history"} and doc.doc_type == "reasoning":
        score += 0.25
        reasons.append("reasoning_boost")
    if intent == "code_why" and doc.doc_type == "code_impact":
        score += 0.24
        reasons.append("code_impact_boost")
        if _code_locator_match(query, text):
            score += 0.12
            reasons.append("code_locator_impact_boost")
    if intent == "code_why" and doc.doc_type == "file_impact":
        score += 0.32
        reasons.append("file_impact_boost")
        if _code_locator_match(query, text):
            score += 0.18
            reasons.append("code_locator_file_rollup_boost")
        elif _query_has_code_locator(query):
            score -= 0.28
            reasons.append("code_locator_mismatch_penalty")
    code_locator_query = _query_has_code_locator(query)
    if intent == "version_flow" and doc.doc_type == "file_impact":
        # FileImpactSummary is the curated per-file rollup that carries the
        # ordered commit/reason packet context. A central file KnowledgeVersion
        # only says "this file exists in active memory"; it is not enough to
        # explain evolution by itself.
        score += 0.36
        reasons.append("version_file_impact_boost")
        if _code_locator_match(query, text):
            score += 0.24
            reasons.append("version_locator_file_rollup_boost")
        elif code_locator_query:
            score -= 0.50
            reasons.append("code_locator_mismatch_penalty")
    if intent == "version_flow" and doc.doc_type == "code_impact":
        score += 0.26
        reasons.append("version_code_impact_boost")
        if _code_locator_match(query, text):
            score += 0.18
            reasons.append("version_locator_code_impact_boost")
        elif code_locator_query:
            score -= 0.40
            reasons.append("code_locator_mismatch_penalty")
    if intent == "version_flow" and doc.doc_type in {"packet", "reasoning"} and code_locator_query:
        if _code_locator_match(query, text):
            score += 0.14
            reasons.append("version_locator_reasoning_context_boost")
        else:
            score -= 0.20
            reasons.append("code_locator_mismatch_penalty")
    if intent in {"code_why", "version_flow"} and doc.doc_type in {"file_ref", "symbol_ref", "code_region_ref"}:
        if code_locator_query:
            score += 0.08
            reasons.append("curated_code_support_boost")
        else:
            score -= 0.14
            reasons.append("broad_query_code_support_penalty")
    node_type = _doc_node_type(doc)
    if intent == "decision_history" and doc.doc_type == "reasoning":
        if node_type == "Decision":
            score += 0.18
            reasons.append("decision_node_boost")
        elif node_type in {"Cause", "Fix", "Constraint"}:
            score += 0.08
            reasons.append("decision_context_boost")
    if "vector" in source_scores:
        vector_score = max(0.0, min(1.0, float(source_scores["vector"])))
        vector_boost = vector_score * max(0.0, float(bi_encoder_weight))
        score += vector_boost
        reasons.append(f"bi_encoder_score:{round(vector_score, 6)}")
        reasons.append(f"bi_encoder_boost:{round(vector_boost, 6)}")
    if intent == "version_flow" and doc.doc_type in {"symbol", "code"}:
        score += 0.25
        reasons.append("version_flow_boost")
        if overlap:
            target_ratio = len(overlap) / max(1, len(scoring_terms))
            score += target_ratio * 0.2
            reasons.append(f"version_target_overlap:{round(target_ratio, 3)}")
        if doc.doc_type == "symbol":
            score += 0.1
            reasons.append("symbol_version_boost")
    if doc.memory_class == "supporting_evidence":
        score -= 0.18
        reasons.append("supporting_evidence_penalty")
        if intent in {"code_why", "decision_history"}:
            score -= 0.10
            reasons.append("answer_query_evidence_penalty")
    if doc.doc_type == "commit":
        score -= 0.12
        reasons.append("commit_hub_penalty")
    if _looks_like_test_artifact(doc) and "test" not in terms:
        score -= 0.08
        reasons.append("test_artifact_penalty")
    role = _doc_impact_role(doc)
    if role == "validation_test" and "test" not in terms:
        score -= 0.10
        reasons.append("validation_support_penalty")
    elif role in {"docs", "config"} and not code_locator_query:
        score -= 0.04
        reasons.append(f"{role}_support_penalty")
    neighbor_text = _normalize(" ".join(f"{n.get('label') or ''} {n.get('summary') or ''}" for n in neighbors))
    if neighbor_text and any(term in neighbor_text for term in terms):
        score += 0.08
        reasons.append("neighbor_overlap")
    score += min(max(doc.importance, 0.0), 1.0) * 0.05
    return score, reasons


def _doc_impact_role(doc: RetrievalDocument) -> str:
    metadata = doc.metadata if isinstance(doc.metadata, dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    return str(metadata.get("impact_role") or metadata.get("primary_impact_role") or node_metadata.get("impact_role") or node_metadata.get("primary_impact_role") or "")


def _topic_terms(query: str, intent: str) -> set[str]:
    terms = set(_expanded_query_terms(query))
    if "hook" in terms:
        # In AMO queries, "Codex hooks" usually names the agent surface.
        # The durable topic is the hook behavior: capture, injection, prompt flow.
        terms = terms.difference(AGENT_CONTEXT_TERMS)
    if intent == "decision_history":
        return terms.difference(DECISION_HISTORY_OPERATOR_TERMS)
    if intent == "code_why":
        return terms.difference(CODE_WHY_OPERATOR_TERMS)
    if intent == "version_flow":
        return terms.difference(VERSION_FLOW_OPERATOR_TERMS)
    return terms


def _code_locator_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or "")):
        lowered = token.lower().replace("\\", "/")
        if not lowered:
            continue
        if "_" in lowered or "::" in lowered or "/" in lowered or "." in lowered:
            terms.add(lowered)
            parts = [part for part in re.split(r"[^a-zA-Z0-9_]+", lowered) if len(part) > 2]
            terms.update(_stem_term(part) for part in parts if part not in QUERY_STOPWORDS)
    return terms


def _code_locator_match(query: str, normalized_doc_text: str) -> bool:
    locator_terms = _code_locator_terms(query)
    if not locator_terms:
        return False
    text = normalized_doc_text.lower()
    return any(term in text for term in locator_terms)


def _primary_rank_text(doc: RetrievalDocument, *, include_code_locator_context: bool = False) -> str:
    if doc.doc_type != "reasoning":
        return _normalize(f"{doc.title} {doc.body}")

    kept: list[str] = [doc.title]
    for raw_line in doc.body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        prefix = line.split(":", 1)[0].strip().lower()
        if prefix in {"changed paths", "linked code", "evidence", "metadata", "paths", "file_path", "symbol"} and not include_code_locator_context:
            continue
        kept.append(line)
    return _normalize(" ".join(kept))


def _doc_node_type(doc: RetrievalDocument) -> str:
    metadata = doc.metadata.get("node_metadata") if isinstance(doc.metadata, dict) else None
    if isinstance(metadata, dict):
        return str(metadata.get("node_type") or "")
    return str(doc.metadata.get("node_type") or "") if isinstance(doc.metadata, dict) else ""


def _central_version_boost(
    doc: RetrievalDocument,
    *,
    intent: str,
    query: str,
    topic_overlap_ratio: float,
) -> float:
    metadata = doc.metadata.get("node_metadata") if isinstance(doc.metadata, dict) else {}
    atom_kind = str(metadata.get("atom_kind") or "") if isinstance(metadata, dict) else ""
    if atom_kind in {"decision", "problem"}:
        if intent == "decision_history":
            if topic_overlap_ratio >= 0.6:
                return 0.85
            if topic_overlap_ratio >= 0.4:
                return 0.45
            return 0.0
        if intent == "code_why" or _query_has_code_locator(query):
            if topic_overlap_ratio >= 0.75:
                return 0.35
            if topic_overlap_ratio >= 0.6:
                return 0.20
            return 0.0
        if topic_overlap_ratio >= 0.6:
            return 0.25
        return 0.0
    if intent == "version_flow":
        # File/commit KnowledgeVersions are identity stubs. Keep them visible,
        # but let curated FileImpact/CodeImpact docs explain the actual
        # evolution when they are available.
        if atom_kind in {"file", "commit"}:
            return 0.25
        return 0.55
    if _query_has_code_locator(query):
        return 0.55
    if atom_kind in {"file", "commit"} and intent == "semantic_search":
        if topic_overlap_ratio >= 0.6:
            return 0.18
        if topic_overlap_ratio >= 0.4:
            return 0.08
        return 0.0
    if topic_overlap_ratio >= 0.6:
        return 0.65
    if topic_overlap_ratio >= 0.4:
        return 0.55
    return 0.0


def _looks_like_test_artifact(doc: RetrievalDocument) -> bool:
    lowered = f"{doc.title} {doc.body}".lower()
    return "tests/" in lowered or "tests\\" in lowered or "test_" in lowered
