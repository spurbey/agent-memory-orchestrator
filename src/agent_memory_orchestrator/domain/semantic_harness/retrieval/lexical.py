from __future__ import annotations

import math
from collections import Counter

from ..projection import HarnessProjectionDocument
from .models import LexicalRetrievalHit
from .models import LexicalRetrievalOptions
from .tokenization import tokenize_text


def search_projection_documents(
    documents: tuple[HarnessProjectionDocument, ...],
    query: str,
    *,
    options: LexicalRetrievalOptions = LexicalRetrievalOptions(),
) -> tuple[LexicalRetrievalHit, ...]:
    """Return BM25-style lexical candidates from projection documents.

    This is candidate discovery only. Callers must ground returned
    `source_node_id` values back to graph nodes before producing agent cards.
    """

    query_terms = tuple(dict.fromkeys(tokenize_text(query)))
    if not documents or not query_terms:
        return ()
    doc_terms = {document.doc_id: tokenize_text(_searchable_text(document)) for document in documents}
    doc_lengths = {doc_id: len(terms) for doc_id, terms in doc_terms.items()}
    avg_doc_length = sum(doc_lengths.values()) / max(1, len(doc_lengths))
    document_frequency = _document_frequency(doc_terms.values())
    scored: list[LexicalRetrievalHit] = []
    for document in documents:
        term_counts = Counter(doc_terms[document.doc_id])
        if not term_counts:
            continue
        term_scores = _term_scores(
            query_terms=query_terms,
            term_counts=term_counts,
            document_frequency=document_frequency,
            document_count=len(documents),
            document_length=doc_lengths[document.doc_id],
            avg_document_length=avg_doc_length,
            options=options,
        )
        if not term_scores:
            continue
        raw_score = sum(term_scores.values()) * _doc_type_boost(document.doc_type)
        normalizer = max(0.0001, sum(_idf(term, document_frequency, len(documents)) for term in query_terms))
        normalized = min(1.0, raw_score / (normalizer * 2.2))
        if normalized < options.min_score:
            continue
        scored.append(
            LexicalRetrievalHit(
                document=document,
                score=round(raw_score, 6),
                normalized_score=round(normalized, 6),
                matched_terms=tuple(sorted(term_scores)),
                term_scores={term: round(score, 6) for term, score in sorted(term_scores.items())},
            )
        )
    return tuple(
        sorted(
            scored,
            key=lambda hit: (-hit.normalized_score, _doc_kind_rank(hit.document.source_kind), hit.document.title, hit.document.doc_id),
        )[: max(1, options.top_k)]
    )


def _searchable_text(document: HarnessProjectionDocument) -> str:
    metadata_text = " ".join(str(value) for value in document.metadata.values() if value)
    return "\n".join((document.title, document.text, metadata_text))


def _document_frequency(doc_term_lists) -> Counter[str]:
    frequency: Counter[str] = Counter()
    for terms in doc_term_lists:
        frequency.update(set(terms))
    return frequency


def _term_scores(
    *,
    query_terms: tuple[str, ...],
    term_counts: Counter[str],
    document_frequency: Counter[str],
    document_count: int,
    document_length: int,
    avg_document_length: float,
    options: LexicalRetrievalOptions,
) -> dict[str, float]:
    scores: dict[str, float] = {}
    length_norm = options.k1 * (1 - options.b + options.b * (document_length / max(1.0, avg_document_length)))
    for term in query_terms:
        term_frequency = term_counts.get(term, 0)
        if term_frequency <= 0:
            continue
        numerator = term_frequency * (options.k1 + 1)
        denominator = term_frequency + length_norm
        scores[term] = _idf(term, document_frequency, document_count) * (numerator / max(0.0001, denominator))
    return scores


def _idf(term: str, document_frequency: Counter[str], document_count: int) -> float:
    frequency = document_frequency.get(term, 0)
    return math.log(1 + (document_count - frequency + 0.5) / (frequency + 0.5))


def _doc_type_boost(doc_type: str) -> float:
    return {
        "symbol_summary": 1.08,
        "file_summary": 1.0,
        "doc_semantic_summary": 0.96,
    }.get(doc_type, 0.9)


def _doc_kind_rank(kind: str) -> int:
    return {"Symbol": 0, "File": 1, "DocString": 2, "DocSection": 3}.get(kind, 9)


__all__ = ["search_projection_documents"]
