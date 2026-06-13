from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from ..models import HarnessEdge
from ..models import HarnessNode
from .models import DocSemanticArtifact


@dataclass(slots=True, frozen=True)
class _FileMentionCandidate:
    node: HarnessNode
    path: str
    lowered_paths: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class _SymbolMentionCandidate:
    node: HarnessNode
    candidates: tuple[tuple[str, str], ...]


def link_doc_mentions(
    artifacts: tuple[DocSemanticArtifact, ...],
    nodes: dict[str, HarnessNode],
) -> tuple[HarnessEdge, ...]:
    file_candidates = _file_candidates(nodes)
    symbol_candidates = _symbol_candidates(nodes)
    edges: list[HarnessEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for artifact in artifacts:
        text = artifact.text
        lowered_text = text.lower()
        for file_candidate in file_candidates:
            if _mentions_file(lowered_text, file_candidate):
                _append_edge(
                    edges,
                    seen,
                    HarnessEdge(
                        source_id=artifact.node.id,
                        target_id=file_candidate.node.id,
                        kind="MENTIONS_FILE",
                        confidence=0.88,
                        metadata={"match": file_candidate.path, "match_type": "exact_path"},
                    ),
                )
        target_node_id = artifact.node.metadata.get("target_node_id")
        for symbol_candidate in symbol_candidates:
            if symbol_candidate.node.id == target_node_id:
                continue
            match_value = _symbol_match(text, lowered_text, symbol_candidate)
            if not match_value:
                continue
            _append_edge(
                edges,
                seen,
                HarnessEdge(
                    source_id=artifact.node.id,
                    target_id=symbol_candidate.node.id,
                    kind="MENTIONS_SYMBOL",
                    confidence=0.76,
                    metadata={"match": match_value, "match_type": "exact_symbol_label"},
                ),
            )
    return tuple(edges)


def _file_candidates(nodes: dict[str, HarnessNode]) -> tuple[_FileMentionCandidate, ...]:
    out: list[_FileMentionCandidate] = []
    for node in nodes.values():
        if node.kind != "File":
            continue
        path = str(node.metadata.get("path") or node.label).replace("\\", "/").strip("/")
        if not path:
            continue
        lowered_paths = tuple(dict.fromkeys((path.lower(), path.replace("/", "\\").lower())))
        out.append(_FileMentionCandidate(node=node, path=path, lowered_paths=lowered_paths))
    return tuple(out)


def _mentions_file(lowered_text: str, candidate: _FileMentionCandidate) -> bool:
    return any(path in lowered_text for path in candidate.lowered_paths)


def _symbol_candidates(nodes: dict[str, HarnessNode]) -> tuple[_SymbolMentionCandidate, ...]:
    out: list[_SymbolMentionCandidate] = []
    for node in nodes.values():
        if node.kind != "Symbol":
            continue
        candidates: list[str] = []
        qualified_name = str(node.metadata.get("qualified_name") or node.label).strip()
        if qualified_name:
            candidates.append(qualified_name)
        short_name = qualified_name.rsplit(".", 1)[-1] if qualified_name else str(node.label)
        if len(short_name) >= 4:
            candidates.append(short_name)
        pairs = tuple((candidate, candidate.lower()) for candidate in _dedupe(candidates))
        if pairs:
            out.append(_SymbolMentionCandidate(node=node, candidates=pairs))
    return tuple(out)


def _symbol_match(text: str, lowered_text: str, symbol_candidate: _SymbolMentionCandidate) -> str:
    for candidate, lowered_candidate in symbol_candidate.candidates:
        if lowered_candidate not in lowered_text:
            continue
        if _contains_tokenish(text, candidate):
            return candidate
    return ""


def _contains_tokenish(text: str, candidate: str) -> bool:
    if not candidate:
        return False
    return _tokenish_pattern(candidate).search(text) is not None


@lru_cache(maxsize=8192)
def _tokenish_pattern(candidate: str) -> re.Pattern[str]:
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(candidate) + r"(?![A-Za-z0-9_.])"
    return re.compile(pattern, flags=re.IGNORECASE)


def _append_edge(edges: list[HarnessEdge], seen: set[tuple[str, str, str]], edge: HarnessEdge) -> None:
    key = (edge.source_id, edge.target_id, edge.kind)
    if key in seen:
        return
    seen.add(key)
    edges.append(edge)


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


__all__ = ["link_doc_mentions"]
