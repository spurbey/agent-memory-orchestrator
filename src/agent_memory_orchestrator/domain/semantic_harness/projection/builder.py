from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..identity import projection_doc_id
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
    docs = [
        doc
        for node in sorted(graph.nodes, key=lambda item: (item.kind, item.id))
        if node.kind in include
        if (doc := _document_for_node(graph.repo_id, node, node_by_id)) is not None
    ]
    return tuple(docs)


def _document_for_node(
    repo_id: str,
    node: HarnessNode,
    node_by_id: dict[str, HarnessNode],
) -> HarnessProjectionDocument | None:
    if node.kind == "File":
        return _file_doc(repo_id, node)
    if node.kind == "Symbol":
        return _symbol_doc(repo_id, node)
    if node.kind in {"DocSection", "DocString"}:
        return _doc_artifact_doc(repo_id, node, node_by_id)
    return None


def _file_doc(repo_id: str, node: HarnessNode) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or node.label)
    language = str(node.metadata.get("language") or "")
    title = f"File {path}"
    text = _compact_lines(
        (
            title,
            f"path: {path}",
            f"language: {language}" if language else "",
            f"summary: {node.summary}" if node.summary else "",
        )
    )
    return _projection_doc(repo_id=repo_id, node=node, doc_type="file_summary", title=title, text=text)


def _symbol_doc(repo_id: str, node: HarnessNode) -> HarnessProjectionDocument:
    path = str(node.metadata.get("path") or "")
    qualified_name = str(node.metadata.get("qualified_name") or node.label)
    symbol_kind = str(node.metadata.get("symbol_kind") or "")
    title = f"Symbol {qualified_name}"
    text = _compact_lines(
        (
            title,
            f"qualified_name: {qualified_name}",
            f"symbol_kind: {symbol_kind}" if symbol_kind else "",
            f"path: {path}" if path else "",
            f"summary: {node.summary}" if node.summary else "",
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


__all__ = ["DEFAULT_PROJECTED_KINDS", "build_projection_documents"]
