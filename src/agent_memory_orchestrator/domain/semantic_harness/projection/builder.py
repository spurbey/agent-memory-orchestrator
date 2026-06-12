from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..identity import projection_doc_id
from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from .models import HarnessProjectionDocument


DEFAULT_PROJECTED_KINDS = frozenset({"File", "Symbol", "DocSection", "DocString"})


def build_projection_documents(
    graph: StructuralHarnessGraph,
    *,
    include_kinds: Iterable[str] = DEFAULT_PROJECTED_KINDS,
) -> tuple[HarnessProjectionDocument, ...]:
    """Build deterministic high-signal documents from graph truth.

    The projection is candidate-discovery input for lexical/vector layers. It
    does not create graph truth and intentionally excludes versions, hunks, raw
    AST-like fragments, and relation occurrences in this bootstrap slice.
    """

    include = set(include_kinds)
    node_by_id = graph.node_by_id()
    graph_index = _ProjectionGraphIndex.from_graph(graph)
    docs = [
        doc
        for node in sorted(graph.nodes, key=lambda item: (item.kind, item.id))
        if node.kind in include
        if (doc := _document_for_node(graph.repo_id, node, node_by_id, graph_index)) is not None
    ]
    return tuple(docs)


def _document_for_node(
    repo_id: str,
    node: HarnessNode,
    node_by_id: dict[str, HarnessNode],
    graph_index: _ProjectionGraphIndex,
) -> HarnessProjectionDocument | None:
    if node.kind == "File":
        return _file_doc(repo_id, node, graph_index)
    if node.kind == "Symbol":
        return _symbol_doc(repo_id, node, graph_index)
    if node.kind in {"DocSection", "DocString"}:
        return _doc_artifact_doc(repo_id, node, node_by_id)
    return None


def _file_doc(repo_id: str, node: HarnessNode, graph_index: _ProjectionGraphIndex) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or node.label)
    language = str(node.metadata.get("language") or "")
    defined_symbols = _neighbor_labels(graph_index.outgoing(node.id, "DEFINES"), graph_index.node_by_id)
    doc_summaries = _doc_summaries(graph_index.incoming(node.id, "DOCUMENTS_FILE"), graph_index.node_by_id)
    title = f"File {path}"
    text = _compact_lines(
        (
            title,
            f"path: {path}",
            f"language: {language}" if language else "",
            f"summary: {node.summary}" if node.summary else "",
            f"defined_symbols: {', '.join(defined_symbols)}" if defined_symbols else "",
            f"docs: {' | '.join(doc_summaries)}" if doc_summaries else "",
        )
    )
    return _projection_doc(repo_id=repo_id, node=node, doc_type="file_summary", title=title, text=text)


def _symbol_doc(repo_id: str, node: HarnessNode, graph_index: _ProjectionGraphIndex) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or "")
    qualified_name = str(node.metadata.get("qualified_name") or node.label)
    symbol_kind = str(node.metadata.get("symbol_kind") or "")
    doc_summaries = _doc_summaries(graph_index.incoming(node.id, "DOCUMENTS_SYMBOL"), graph_index.node_by_id)
    mentioned_by = _doc_summaries(graph_index.incoming(node.id, "MENTIONS_SYMBOL"), graph_index.node_by_id)
    calls = _neighbor_labels(graph_index.outgoing(node.id, "CALLS"), graph_index.node_by_id)
    called_by = _neighbor_labels(graph_index.incoming(node.id, "CALLS"), graph_index.node_by_id, source=True)
    title = f"Symbol {qualified_name}"
    text = _compact_lines(
        (
            title,
            f"qualified_name: {qualified_name}",
            f"symbol_kind: {symbol_kind}" if symbol_kind else "",
            f"path: {path}" if path else "",
            f"summary: {node.summary}" if node.summary else "",
            f"docs: {' | '.join(doc_summaries)}" if doc_summaries else "",
            f"mentioned_by_docs: {' | '.join(mentioned_by)}" if mentioned_by else "",
            f"calls: {', '.join(calls)}" if calls else "",
            f"called_by: {', '.join(called_by)}" if called_by else "",
        )
    )
    return _projection_doc(repo_id=repo_id, node=node, doc_type="symbol_summary", title=title, text=text)


def _doc_artifact_doc(
    repo_id: str,
    node: HarnessNode,
    node_by_id: dict[str, HarnessNode],
) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or "")
    doc_kind = str(node.metadata.get("doc_kind") or node.kind)
    target = node_by_id.get(str(node.metadata.get("target_node_id") or ""))
    title = f"{node.kind} {node.label}"
    target_text = f"target: {target.kind} {target.label}" if target is not None else ""
    text = _compact_lines(
        (
            title,
            f"path: {path}" if path else "",
            f"doc_kind: {doc_kind}",
            target_text,
            f"summary: {node.summary}" if node.summary else "",
            f"content: {node.metadata.get('content_excerpt')}" if node.metadata.get("content_excerpt") else "",
        )
    )
    return _projection_doc(
        repo_id=repo_id,
        node=node,
        doc_type="doc_semantic_summary",
        title=title,
        text=text,
        metadata_extra={"target_node_id": target.id if target is not None else ""},
    )


def _projection_doc(
    *,
    repo_id: str,
    node: HarnessNode,
    doc_type: str,
    title: str,
    text: str,
    metadata_extra: dict[str, Any] | None = None,
) -> HarnessProjectionDocument:
    metadata = {
        "path": node.metadata.get("path", ""),
        "status": node.status,
        "projection_source": "semantic_harness_graph",
        **(metadata_extra or {}),
    }
    return HarnessProjectionDocument(
        doc_id=projection_doc_id(repo_id, node.id, doc_type),
        repo_id=repo_id,
        source_node_id=node.id,
        source_kind=node.kind,
        doc_type=doc_type,
        title=title,
        text=text,
        metadata=metadata,
    )


def _compact_lines(lines: Iterable[str]) -> str:
    return "\n".join(line.strip() for line in lines if line and line.strip())


def _neighbor_labels(
    edges: tuple[HarnessEdge, ...],
    node_by_id: dict[str, HarnessNode],
    *,
    source: bool = False,
    limit: int = 8,
) -> tuple[str, ...]:
    labels: list[str] = []
    for edge in edges:
        neighbor_id = edge.source_id if source else edge.target_id
        node = node_by_id.get(neighbor_id)
        if node is None:
            continue
        labels.append(node.label)
    return tuple(sorted(dict.fromkeys(labels))[:limit])


def _doc_summaries(
    edges: tuple[HarnessEdge, ...],
    node_by_id: dict[str, HarnessNode],
    *,
    limit: int = 4,
) -> tuple[str, ...]:
    summaries: list[str] = []
    for edge in edges:
        node = node_by_id.get(edge.source_id)
        if node is None:
            continue
        value = node.summary or str(node.metadata.get("content_excerpt") or "")
        if value:
            summaries.append(value[:220])
    return tuple(dict.fromkeys(summaries))[:limit]


class _ProjectionGraphIndex:
    def __init__(self, node_by_id: dict[str, HarnessNode], outgoing: dict[tuple[str, str], list[HarnessEdge]], incoming: dict[tuple[str, str], list[HarnessEdge]]) -> None:
        self.node_by_id = node_by_id
        self._outgoing = outgoing
        self._incoming = incoming

    @classmethod
    def from_graph(cls, graph: StructuralHarnessGraph) -> _ProjectionGraphIndex:
        outgoing: dict[tuple[str, str], list[HarnessEdge]] = {}
        incoming: dict[tuple[str, str], list[HarnessEdge]] = {}
        for edge in graph.edges:
            outgoing.setdefault((edge.source_id, edge.kind), []).append(edge)
            incoming.setdefault((edge.target_id, edge.kind), []).append(edge)
        return cls(graph.node_by_id(), outgoing, incoming)

    def outgoing(self, node_id: str, kind: str) -> tuple[HarnessEdge, ...]:
        return tuple(self._outgoing.get((node_id, kind), ()))

    def incoming(self, node_id: str, kind: str) -> tuple[HarnessEdge, ...]:
        return tuple(self._incoming.get((node_id, kind), ()))


__all__ = ["DEFAULT_PROJECTED_KINDS", "build_projection_documents"]
