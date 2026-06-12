from __future__ import annotations

import re

from ..models import HarnessEdge
from ..models import HarnessNode
from .models import DocSemanticArtifact


def link_doc_mentions(
    artifacts: tuple[DocSemanticArtifact, ...],
    nodes: dict[str, HarnessNode],
) -> tuple[HarnessEdge, ...]:
    file_nodes = tuple(node for node in nodes.values() if node.kind == "File")
    symbol_nodes = tuple(node for node in nodes.values() if node.kind == "Symbol")
    edges: list[HarnessEdge] = []
    seen: set[tuple[str, str, str]] = set()
    for artifact in artifacts:
        text = artifact.text
        for file_node in file_nodes:
            if _mentions_file(text, str(file_node.metadata.get("path") or file_node.label)):
                _append_edge(
                    edges,
                    seen,
                    HarnessEdge(
                        source_id=artifact.node.id,
                        target_id=file_node.id,
                        kind="MENTIONS_FILE",
                        confidence=0.88,
                        metadata={"match": str(file_node.metadata.get("path") or file_node.label), "match_type": "exact_path"},
                    ),
                )
        for symbol_node in symbol_nodes:
            if symbol_node.id == artifact.node.metadata.get("target_node_id"):
                continue
            match_value = _symbol_match(text, symbol_node)
            if not match_value:
                continue
            _append_edge(
                edges,
                seen,
                HarnessEdge(
                    source_id=artifact.node.id,
                    target_id=symbol_node.id,
                    kind="MENTIONS_SYMBOL",
                    confidence=0.76,
                    metadata={"match": match_value, "match_type": "exact_symbol_label"},
                ),
            )
    return tuple(edges)


def _mentions_file(text: str, path: str) -> bool:
    normalized = path.replace("\\", "/").strip("/")
    if not normalized:
        return False
    candidates = {normalized, normalized.replace("/", "\\")}
    lowered = text.lower()
    return any(candidate.lower() in lowered for candidate in candidates)


def _symbol_match(text: str, node: HarnessNode) -> str:
    candidates = []
    qualified_name = str(node.metadata.get("qualified_name") or node.label).strip()
    if qualified_name:
        candidates.append(qualified_name)
    short_name = qualified_name.rsplit(".", 1)[-1] if qualified_name else str(node.label)
    if len(short_name) >= 4:
        candidates.append(short_name)
    for candidate in _dedupe(candidates):
        if _contains_tokenish(text, candidate):
            return candidate
    return ""


def _contains_tokenish(text: str, candidate: str) -> bool:
    if not candidate:
        return False
    pattern = r"(?<![A-Za-z0-9_])" + re.escape(candidate) + r"(?![A-Za-z0-9_.])"
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


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
