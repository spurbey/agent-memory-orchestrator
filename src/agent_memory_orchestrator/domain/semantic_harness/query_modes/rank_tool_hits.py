from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any

from ..identity import normalize_file_path
from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from ..projection.models import HarnessProjectionDocument


HASH_COSINE_METHOD = "hash_token_char_cosine_v1"


@dataclass(slots=True, frozen=True)
class RankedToolLine:
    file_path: str
    line: int
    text: str

    def as_dict(self) -> dict[str, object]:
        return {"file_path": self.file_path, "line": self.line, "text": self.text}


@dataclass(slots=True, frozen=True)
class RankedToolHit:
    path: str
    file_node_id: str
    score: float
    match_count: int
    line_refs: tuple[RankedToolLine, ...]
    symbol_node_ids: tuple[str, ...]
    semantic_similarity: float
    semantic_doc_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "file_node_id": self.file_node_id,
            "score": self.score,
            "match_count": self.match_count,
            "line_refs": [line.as_dict() for line in self.line_refs],
            "symbol_node_ids": list(self.symbol_node_ids),
            "semantic_similarity": self.semantic_similarity,
            "semantic_doc_ids": list(self.semantic_doc_ids),
            "reason_codes": list(self.reason_codes),
        }


@dataclass(slots=True, frozen=True)
class RankToolHitsResult:
    status: str
    ranked_hits: tuple[RankedToolHit, ...]
    query_text: str
    raw_ref: str
    embedding_backend: str
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "ranked_hits": [hit.as_dict() for hit in self.ranked_hits],
            "query_text": self.query_text,
            "raw_ref": self.raw_ref,
            "embedding_backend": self.embedding_backend,
            "warnings": list(self.warnings),
        }


def answer_rank_tool_hits(
    graph: StructuralHarnessGraph,
    *,
    user_goal: str = "",
    recent_tool_result: dict[str, Any] | None = None,
    already_seen_node_ids: tuple[str, ...] = (),
    max_results: int = 8,
    projection_documents: tuple[HarnessProjectionDocument, ...] | None = None,
) -> RankToolHitsResult:
    recent = recent_tool_result or {}
    raw_text = _tool_result_text(recent)
    line_refs = parse_rank_tool_lines(recent)
    if not line_refs:
        return RankToolHitsResult(
            status="unavailable",
            ranked_hits=(),
            query_text=_query_text(user_goal=user_goal, recent_tool_result=recent),
            raw_ref=_raw_ref(raw_text, recent),
            embedding_backend=HASH_COSINE_METHOD,
            warnings=("no_rankable_tool_lines",),
        )

    node_index = _GraphNodeIndex(graph)
    candidate_paths = tuple(dict.fromkeys(line.file_path for line in line_refs))
    candidates = tuple(
        _Candidate(path=path, file_node=file_node, lines=tuple(line for line in line_refs if line.file_path == path))
        for path in candidate_paths
        if (file_node := node_index.file_by_path.get(path)) is not None
    )
    if not candidates:
        return RankToolHitsResult(
            status="low_confidence",
            ranked_hits=(),
            query_text=_query_text(user_goal=user_goal, recent_tool_result=recent),
            raw_ref=_raw_ref(raw_text, recent),
            embedding_backend=HASH_COSINE_METHOD,
            warnings=("no_graph_grounded_candidates",),
        )

    docs = projection_documents if projection_documents is not None else _build_projection_documents(graph)
    query_text = _query_text(user_goal=user_goal, recent_tool_result=recent)
    seen_node_ids = set(already_seen_node_ids)
    ranked = tuple(
        _rank_candidate(
            candidate,
            graph=graph,
            node_index=node_index,
            projection_documents=docs,
            query_text=query_text,
            seen_node_ids=seen_node_ids,
        )
        for candidate in candidates
    )
    ranked = tuple(sorted(ranked, key=lambda hit: (-hit.score, hit.path))[: max(1, max_results)])
    return RankToolHitsResult(
        status="ready" if ranked else "low_confidence",
        ranked_hits=ranked,
        query_text=query_text,
        raw_ref=_raw_ref(raw_text, recent),
        embedding_backend=HASH_COSINE_METHOD,
        warnings=("candidate_discovery_only", "embedding_backend:hash_fallback"),
    )


def parse_rank_tool_lines(recent_tool_result: dict[str, Any]) -> tuple[RankedToolLine, ...]:
    """Return normalized rankable file/line rows without requiring a graph."""

    return _parse_search_lines(_tool_result_text(recent_tool_result))


@dataclass(slots=True, frozen=True)
class _Candidate:
    path: str
    file_node: HarnessNode
    lines: tuple[RankedToolLine, ...]


class _GraphNodeIndex:
    def __init__(self, graph: StructuralHarnessGraph) -> None:
        self.node_by_id = graph.node_by_id()
        self.file_by_path = {
            normalize_file_path(str(node.metadata.get("path") or node.label)).lower(): node
            for node in graph.nodes_by_kind("File")
        }
        self.symbols_by_path: dict[str, tuple[HarnessNode, ...]] = {}
        symbols_by_path: dict[str, list[HarnessNode]] = {}
        for node in graph.nodes_by_kind("Symbol"):
            path = normalize_file_path(str(node.metadata.get("path") or "")).lower()
            if path:
                symbols_by_path.setdefault(path, []).append(node)
        self.symbols_by_path = {
            path: tuple(sorted(nodes, key=lambda item: _symbol_span_size(item))) for path, nodes in symbols_by_path.items()
        }

    def symbols_for_lines(self, path: str, lines: tuple[RankedToolLine, ...]) -> tuple[HarnessNode, ...]:
        symbols = self.symbols_by_path.get(path, ())
        out: list[HarnessNode] = []
        seen: set[str] = set()
        for line in lines:
            for symbol in symbols:
                if _line_inside_symbol(line.line, symbol):
                    if symbol.id not in seen:
                        seen.add(symbol.id)
                        out.append(symbol)
                    break
        return tuple(out)


def _rank_candidate(
    candidate: _Candidate,
    *,
    graph: StructuralHarnessGraph,
    node_index: _GraphNodeIndex,
    projection_documents: tuple[HarnessProjectionDocument, ...],
    query_text: str,
    seen_node_ids: set[str],
) -> RankedToolHit:
    symbols = node_index.symbols_for_lines(candidate.path, candidate.lines)
    candidate_node_ids = (candidate.file_node.id, *(symbol.id for symbol in symbols))
    candidate_docs = _documents_for_candidate(
        projection_documents,
        candidate_node_ids=set(candidate_node_ids),
        path=candidate.path,
    )
    semantic_score, semantic_docs = _candidate_doc_similarity(candidate_docs, query_text)
    match_score = _match_strength(candidate.lines, query_text=query_text)
    grounding_score = 1.0 if symbols else 0.58
    path_role_score = _path_role_score(candidate.path, query_text)
    validation_score = _validation_relevance(candidate.path, query_text)
    seen_penalty = 0.18 if candidate.file_node.id in seen_node_ids else 0.0
    score = (
        0.25 * match_score
        + 0.20 * grounding_score
        + 0.30 * semantic_score
        + 0.15 * path_role_score
        + 0.10 * validation_score
        - seen_penalty
    )
    reason_codes = _reason_codes(
        match_score=match_score,
        grounding_score=grounding_score,
        semantic_score=semantic_score,
        semantic_docs=semantic_docs,
        path_role_score=path_role_score,
        validation_score=validation_score,
        seen_penalty=seen_penalty,
    )
    return RankedToolHit(
        path=candidate.path,
        file_node_id=candidate.file_node.id,
        score=round(max(0.0, min(1.0, score)), 4),
        match_count=len(candidate.lines),
        line_refs=candidate.lines[:5],
        symbol_node_ids=tuple(symbol.id for symbol in symbols[:5]),
        semantic_similarity=round(semantic_score, 4),
        semantic_doc_ids=semantic_docs,
        reason_codes=reason_codes,
    )


def _parse_search_lines(text: str) -> tuple[RankedToolLine, ...]:
    refs: list[RankedToolLine] = []
    for raw in str(text or "").splitlines():
        match = re.match(r"^(?P<path>(?![A-Za-z]:)[^:\r\n]+):(?P<line>\d+):(?P<text>.*)$", raw.strip())
        if not match:
            continue
        path = normalize_file_path(match.group("path")).lower()
        if not path or path.startswith("../") or "//" in path:
            continue
        refs.append(
            RankedToolLine(
                file_path=path,
                line=int(match.group("line")),
                text=match.group("text").strip()[:240],
            )
        )
    return tuple(dict.fromkeys(refs))


def _tool_result_text(recent_tool_result: dict[str, Any]) -> str:
    for key in ("text", "tool_response", "response", "stdout", "output", "response_excerpt"):
        value = recent_tool_result.get(key)
        if value:
            return str(value)
    return ""


def _query_text(*, user_goal: str, recent_tool_result: dict[str, Any]) -> str:
    parts: list[str] = [str(user_goal or "")]
    for key in ("user_prompt", "captured_user_prompt", "latest_user_prompt"):
        if value := recent_tool_result.get(key):
            parts.append(str(value))
    search_terms = recent_tool_result.get("search_terms")
    if isinstance(search_terms, (list, tuple)):
        parts.extend(str(term) for term in search_terms)
    elif search_terms:
        parts.append(str(search_terms))
    if not any(part.strip() for part in parts):
        parts.append(str(recent_tool_result.get("command") or ""))
    return " ".join(part.strip() for part in parts if part and part.strip())


def _raw_ref(raw_text: str, recent_tool_result: dict[str, Any]) -> str:
    if value := recent_tool_result.get("raw_ref"):
        return str(value)
    if value := recent_tool_result.get("raw_output_hash"):
        return f"sha256:{value}"
    digest = hashlib.sha256(str(raw_text or "").encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


def _documents_for_candidate(
    documents: tuple[HarnessProjectionDocument, ...],
    *,
    candidate_node_ids: set[str],
    path: str,
) -> tuple[HarnessProjectionDocument, ...]:
    out: list[HarnessProjectionDocument] = []
    seen_doc_ids: set[str] = set()
    for document in documents:
        should_include = False
        if document.source_node_id in candidate_node_ids:
            should_include = True
        else:
            metadata = document.metadata
            if str(metadata.get("path") or "").replace("\\", "/").lower() == path:
                should_include = True
            else:
                linked_ids = {str(metadata.get("target_node_id") or ""), str(metadata.get("anchor_node_id") or "")}
                anchor_ids = metadata.get("anchor_node_ids")
                if isinstance(anchor_ids, (list, tuple)):
                    linked_ids.update(str(value) for value in anchor_ids)
                should_include = bool(candidate_node_ids & {value for value in linked_ids if value})
        if should_include and document.doc_id not in seen_doc_ids:
            seen_doc_ids.add(document.doc_id)
            out.append(document)
    return tuple(out)


def _candidate_doc_similarity(documents: tuple[HarnessProjectionDocument, ...], query_text: str) -> tuple[float, tuple[str, ...]]:
    if not documents or not query_text.strip():
        return 0.0, ()
    lexical_score, lexical_docs = _lexical_similarity(documents, query_text)
    vector_score, vector_docs = _semantic_similarity(documents, query_text)
    if lexical_score >= vector_score:
        return lexical_score, lexical_docs or vector_docs
    return vector_score, vector_docs or lexical_docs


def _lexical_similarity(documents: tuple[HarnessProjectionDocument, ...], query_text: str) -> tuple[float, tuple[str, ...]]:
    from ..retrieval.lexical import search_projection_documents
    from ..retrieval.models import LexicalRetrievalOptions

    hits = search_projection_documents(
        documents,
        query_text,
        options=LexicalRetrievalOptions(top_k=max(1, min(5, len(documents))), min_score=0.0),
    )
    if not hits:
        return 0.0, ()
    top_scores = [hit.normalized_score for hit in hits[:3]]
    score = max(top_scores) * 0.78 + (sum(top_scores) / len(top_scores)) * 0.22
    return min(1.0, score), tuple(hit.document.doc_id for hit in hits[:3])


def _semantic_similarity(documents: tuple[HarnessProjectionDocument, ...], query_text: str) -> tuple[float, tuple[str, ...]]:
    from ..retrieval.models import VectorRetrievalOptions
    from ..retrieval.vector import search_projection_documents_vector

    hits = search_projection_documents_vector(
        documents,
        query_text,
        options=VectorRetrievalOptions(top_k=max(1, min(5, len(documents))), min_score=0.0),
    )
    if not hits:
        return 0.0, ()
    top_scores = [hit.score for hit in hits[:3]]
    score = max(top_scores) * 0.72 + (sum(top_scores) / len(top_scores)) * 0.28
    return min(1.0, score), tuple(hit.document.doc_id for hit in hits[:3])


def _match_strength(lines: tuple[RankedToolLine, ...], *, query_text: str) -> float:
    count_score = min(1.0, math.log2(len(lines) + 1) / 4.0)
    query_terms = set(_tokenize_text(query_text))
    if not query_terms:
        return round(count_score, 4)
    overlap_scores = []
    for line in lines:
        line_terms = set(_tokenize_text(line.text))
        overlap_scores.append(len(query_terms & line_terms) / max(1, min(len(query_terms), 8)))
    best_overlap = max(overlap_scores, default=0.0)
    return round(min(1.0, 0.62 * count_score + 0.38 * best_overlap), 4)


def _path_role_score(path: str, query_text: str) -> float:
    query_terms = set(_tokenize_text(query_text))
    if path.startswith("src/"):
        return 0.92
    if path.startswith("tests/"):
        return 0.9 if query_terms & {"test", "tests", "pytest", "validation", "failure"} else 0.54
    if path.startswith("docs/"):
        return 0.86 if query_terms & {"doc", "docs", "contract", "readme", "plan"} else 0.48
    if path.startswith(("scripts/", "npm/", "apps/")):
        return 0.68
    return 0.5


def _validation_relevance(path: str, query_text: str) -> float:
    terms = set(_tokenize_text(query_text))
    if path.startswith("tests/"):
        return 1.0 if terms & {"test", "tests", "pytest", "validation", "failed", "failure"} else 0.45
    if terms & {"test", "tests", "pytest", "validation", "failed", "failure"}:
        return 0.34
    return 0.2


def _reason_codes(
    *,
    match_score: float,
    grounding_score: float,
    semantic_score: float,
    semantic_docs: tuple[str, ...],
    path_role_score: float,
    validation_score: float,
    seen_penalty: float,
) -> tuple[str, ...]:
    reasons: list[str] = [f"rg_match_strength:{match_score:.2f}"]
    reasons.append("line_maps_to_symbol" if grounding_score >= 1.0 else "file_only_grounding")
    if semantic_docs:
        reasons.append(f"candidate_local_semantic_similarity:{semantic_score:.2f}")
    if path_role_score >= 0.85:
        reasons.append("path_role_high")
    if validation_score >= 0.9:
        reasons.append("validation_relevant")
    if seen_penalty:
        reasons.append("already_seen_penalty")
    return tuple(reasons)


def _build_projection_documents(graph: StructuralHarnessGraph) -> tuple[HarnessProjectionDocument, ...]:
    from ..projection.builder import build_projection_documents

    return build_projection_documents(graph)


def _tokenize_text(text: str) -> tuple[str, ...]:
    from ..retrieval.tokenization import tokenize_text

    return tokenize_text(text)


def _line_inside_symbol(line: int, node: HarnessNode) -> bool:
    start = int(node.metadata.get("line_start") or 0)
    end = int(node.metadata.get("line_end") or 0)
    return start <= line <= end if start and end else False


def _symbol_span_size(node: HarnessNode) -> int:
    start = int(node.metadata.get("line_start") or 0)
    end = int(node.metadata.get("line_end") or start)
    return max(0, end - start)


__all__ = [
    "RankToolHitsResult",
    "RankedToolHit",
    "RankedToolLine",
    "answer_rank_tool_hits",
    "parse_rank_tool_lines",
]
