from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import Any

from ..identity import projection_doc_id
from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import StructuralHarnessGraph
from ..snapshots import graph_snapshot_identity
from .identity import DEFAULT_PROJECTION_VERSION
from .identity import projection_set_id
from .models import HarnessProjectionDocument
from .models import HarnessProjectionSet
from .semantic_facts import build_semantic_fact_projection_documents


DEFAULT_PROJECTED_KINDS = frozenset({"File", "Symbol", "DocSection", "DocString"})


def build_projection_documents(
    graph: StructuralHarnessGraph,
    *,
    include_kinds: Iterable[str] = DEFAULT_PROJECTED_KINDS,
    include_semantic_facts: bool = True,
    include_review_only_semantic_facts: bool = False,
) -> tuple[HarnessProjectionDocument, ...]:
    """Build deterministic high-signal documents from graph truth.

    The projection is candidate-discovery input for lexical/vector layers. It
    does not create graph truth and intentionally excludes versions, hunks, raw
    AST-like fragments, and relation occurrences in this bootstrap slice.
    """

    include = set(include_kinds)
    node_by_id = graph.node_by_id()
    docs = [
        doc
        for node in sorted(graph.nodes, key=lambda item: (item.kind, item.id))
        if node.kind in include
        if (doc := _document_for_node(graph, node, node_by_id)) is not None
    ]
    if include_semantic_facts:
        docs.extend(
            build_semantic_fact_projection_documents(
                graph,
                include_review_only=include_review_only_semantic_facts,
            )
        )
    return tuple(docs)


def build_projection_set(
    graph: StructuralHarnessGraph,
    *,
    include_kinds: Iterable[str] = DEFAULT_PROJECTED_KINDS,
    projection_version: str = DEFAULT_PROJECTION_VERSION,
    include_semantic_facts: bool = True,
    include_review_only_semantic_facts: bool = False,
) -> HarnessProjectionSet:
    snapshot = graph_snapshot_identity(graph)
    documents = build_projection_documents(
        graph,
        include_kinds=include_kinds,
        include_semantic_facts=include_semantic_facts,
        include_review_only_semantic_facts=include_review_only_semantic_facts,
    )
    document_content_hash = _document_content_hash(documents)
    return HarnessProjectionSet(
        repo_id=graph.repo_id,
        projection_id=projection_set_id(
            snapshot.graph_snapshot_id,
            projection_version=projection_version,
            document_content_hash=document_content_hash,
        ),
        projection_version=projection_version,
        graph_snapshot_id=snapshot.graph_snapshot_id,
        graph_schema_version=snapshot.graph_schema_version,
        documents=documents,
    )


def _document_for_node(
    graph: StructuralHarnessGraph,
    node: HarnessNode,
    node_by_id: dict[str, HarnessNode],
) -> HarnessProjectionDocument | None:
    if node.kind == "File":
        return _file_doc(graph, node)
    if node.kind == "Symbol":
        return _symbol_doc(graph, node)
    if node.kind in {"DocSection", "DocString"}:
        return _doc_artifact_doc(graph.repo_id, node, node_by_id)
    return None


def _file_doc(graph: StructuralHarnessGraph, node: HarnessNode) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or node.label)
    language = str(node.metadata.get("language") or "")
    node_by_id = graph.node_by_id()
    defined_symbols = _neighbor_labels(graph.outgoing(node.id, kind="DEFINES"), node_by_id)
    doc_summaries = _doc_summaries(graph.incoming(node.id, kind="DOCUMENTS_FILE"), node_by_id)
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
    return _projection_doc(repo_id=graph.repo_id, node=node, doc_type="file_summary", title=title, text=text)


def _symbol_doc(graph: StructuralHarnessGraph, node: HarnessNode) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or "")
    qualified_name = str(node.metadata.get("qualified_name") or node.label)
    symbol_kind = str(node.metadata.get("symbol_kind") or "")
    node_by_id = graph.node_by_id()
    doc_summaries = _doc_summaries(graph.incoming(node.id, kind="DOCUMENTS_SYMBOL"), node_by_id)
    mentioned_by = _doc_summaries(graph.incoming(node.id, kind="MENTIONS_SYMBOL"), node_by_id)
    calls = _neighbor_labels(graph.outgoing(node.id, kind="CALLS"), node_by_id)
    called_by = _neighbor_labels(graph.incoming(node.id, kind="CALLS"), node_by_id, source=True)
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
    return _projection_doc(repo_id=graph.repo_id, node=node, doc_type="symbol_summary", title=title, text=text)


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


def _document_content_hash(documents: tuple[HarnessProjectionDocument, ...]) -> str:
    stable_parts: list[str] = []
    for document in sorted(documents, key=lambda item: item.doc_id):
        stable_parts.append(document.doc_id)
        stable_parts.append(document.content_hash)
        stable_parts.append(repr(tuple(sorted((str(key), repr(value)) for key, value in document.metadata.items()))))
    return hashlib.sha256("\n".join(stable_parts).encode("utf-8")).hexdigest()


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


__all__ = ["DEFAULT_PROJECTED_KINDS", "build_projection_documents", "build_projection_set"]
