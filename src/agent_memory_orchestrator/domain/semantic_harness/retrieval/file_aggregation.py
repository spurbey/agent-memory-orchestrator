from __future__ import annotations

from dataclasses import dataclass

from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from .models import LexicalRetrievalHit
from .tokenization import tokenize_text


@dataclass(slots=True, frozen=True)
class FileRetrievalCandidate:
    file_node: HarnessNode
    score: float
    matched_terms: tuple[str, ...]
    source_titles: tuple[str, ...]


def aggregate_lexical_hits_to_files(
    graph: StructuralHarnessGraph,
    hits: tuple[LexicalRetrievalHit, ...],
    *,
    query_text: str,
    limit: int,
) -> tuple[FileRetrievalCandidate, ...]:
    """Collapse weak symbol/doc hits into source-file candidates.

    Unanchored edit planning needs "which file should I inspect first" more
    than "which low-confidence symbol happened to share two terms." This helper
    keeps graph truth unchanged and only changes candidate presentation.
    """

    if limit <= 0:
        return ()
    node_by_id = graph.node_by_id()
    file_by_path = {
        str(node.metadata.get("path") or node.label): node
        for node in graph.nodes
        if node.kind == "File" and _is_source_candidate_path(str(node.metadata.get("path") or node.label))
    }
    query_terms = set(tokenize_text(query_text))
    scores: dict[str, float] = {}
    terms: dict[str, set[str]] = {}
    titles: dict[str, list[str]] = {}
    for hit in hits:
        source = node_by_id.get(hit.document.source_node_id)
        if source is None:
            continue
        path = str(source.metadata.get("path") or hit.document.metadata.get("path") or "")
        file_node = file_by_path.get(path)
        if file_node is None:
            continue
        weighted_score = hit.normalized_score * _source_kind_boost(hit.document.source_kind)
        weighted_score *= _path_overlap_boost(path=path, query_terms=query_terms)
        scores[path] = scores.get(path, 0.0) + weighted_score
        terms.setdefault(path, set()).update(hit.matched_terms)
        if hit.document.title not in titles.setdefault(path, []):
            titles[path].append(hit.document.title)
    candidates = [
        FileRetrievalCandidate(
            file_node=file_by_path[path],
            score=round(score, 6),
            matched_terms=tuple(sorted(terms.get(path, ()))),
            source_titles=tuple(titles.get(path, ())[:5]),
        )
        for path, score in scores.items()
        if score >= 0.12
    ]
    return tuple(sorted(candidates, key=lambda item: (-item.score, item.file_node.id))[:limit])


def _is_source_candidate_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    if normalized.startswith(("tests/", "test/", "docs/")):
        return False
    if normalized.endswith((".md", ".rst", ".txt")):
        return False
    return bool(normalized)


def _source_kind_boost(kind: str) -> float:
    return {"File": 1.18, "Symbol": 1.0, "DocString": 0.92}.get(kind, 0.88)


def _path_overlap_boost(*, path: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 1.0
    path_terms = set(tokenize_text(path))
    overlap = path_terms & query_terms
    return 1.0 + min(0.35, 0.08 * len(overlap))


__all__ = ["FileRetrievalCandidate", "aggregate_lexical_hits_to_files"]
