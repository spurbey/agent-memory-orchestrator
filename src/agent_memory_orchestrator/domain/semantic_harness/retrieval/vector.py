from __future__ import annotations

from ..projection import HarnessProjectionDocument
from .embeddings import HASH_COSINE_METHOD
from .embeddings import cosine_similarity
from .embeddings import hash_embed_text
from .embeddings import hash_embedding_features
from .models import VectorRetrievalHit
from .models import VectorRetrievalOptions


def search_projection_documents_vector(
    documents: tuple[HarnessProjectionDocument, ...],
    query: str,
    *,
    options: VectorRetrievalOptions = VectorRetrievalOptions(),
) -> tuple[VectorRetrievalHit, ...]:
    """Return hash-vector cosine candidates from projection documents.

    This is a deterministic local fallback for semantic-ish discovery. It is
    still candidate discovery only; callers must ground hits to graph nodes.
    """

    query_vector = hash_embed_text(query, options=options.embedding)
    query_features = hash_embedding_features(query, options=options.embedding)
    if not documents or not any(query_vector):
        return ()
    scored: list[VectorRetrievalHit] = []
    for document in documents:
        doc_text = _searchable_text(document)
        doc_vector = hash_embed_text(doc_text, options=options.embedding)
        score = cosine_similarity(query_vector, doc_vector) * _doc_type_boost(document.doc_type)
        if score < options.min_score:
            continue
        doc_features = hash_embedding_features(doc_text, options=options.embedding)
        scored.append(
            VectorRetrievalHit(
                document=document,
                score=round(min(1.0, score), 6),
                matched_features=_matched_features(query_features, doc_features),
                embedding_method=HASH_COSINE_METHOD,
            )
        )
    return tuple(
        sorted(
            scored,
            key=lambda hit: (-hit.score, _doc_kind_rank(hit.document.source_kind), hit.document.title, hit.document.doc_id),
        )[: max(1, options.top_k)]
    )


def _searchable_text(document: HarnessProjectionDocument) -> str:
    metadata_text = " ".join(str(value) for value in document.metadata.values() if value)
    return "\n".join((document.title, document.text, metadata_text))


def _matched_features(query_features, doc_features, *, limit: int = 8) -> tuple[str, ...]:
    overlap = set(query_features) & set(doc_features)
    readable = sorted(feature.replace("tok:", "").replace("chr:", "ngram:") for feature in overlap)
    return tuple(readable[:limit])


def _doc_type_boost(doc_type: str) -> float:
    return {
        "symbol_summary": 1.05,
        "file_summary": 0.98,
        "doc_semantic_summary": 0.96,
    }.get(doc_type, 0.9)


def _doc_kind_rank(kind: str) -> int:
    return {"Symbol": 0, "File": 1, "DocString": 2, "DocSection": 3}.get(kind, 9)


__all__ = ["search_projection_documents_vector"]
