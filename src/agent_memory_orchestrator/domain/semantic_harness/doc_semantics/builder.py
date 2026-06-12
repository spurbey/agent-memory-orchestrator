from __future__ import annotations

from ..identity import file_id
from ..identity import normalize_file_path
from ..models import HarnessEdge
from ..models import HarnessNode
from ..models import SourceFile
from .linking import link_doc_mentions
from .markdown import extract_markdown_doc_sections
from .models import DocSemanticArtifact
from .python_docstrings import extract_python_docstrings


def add_doc_semantics(
    *,
    repo_id: str,
    sources: tuple[SourceFile, ...],
    nodes: dict[str, HarnessNode],
    edges: list[HarnessEdge],
) -> None:
    """Add deterministic docs/docstring semantics to an existing structural graph.

    This stage only creates graph-grounded evidence from exact docs/docstring
    structure. Fuzzy discovery, embeddings, and LLM summaries are later proposal
    layers and do not run here.
    """

    artifacts: list[DocSemanticArtifact] = []
    semantic_edges: list[HarnessEdge] = []
    node_ids = set(nodes)
    for source in sources:
        path = normalize_file_path(source.path)
        file_node_id = file_id(repo_id, path)
        if file_node_id not in nodes:
            continue
        artifacts.extend(extract_markdown_doc_sections(repo_id, source, file_node_id))
        for artifact, edge in extract_python_docstrings(repo_id, source, file_node_id, node_ids):
            artifacts.append(artifact)
            semantic_edges.append(edge)
    for artifact in artifacts:
        nodes[artifact.node.id] = artifact.node
        semantic_edges.append(
            HarnessEdge(
                source_id=artifact.source_file_id,
                target_id=artifact.node.id,
                kind="CONTAINS",
                confidence=0.95,
                metadata={"containment": "doc_semantic_artifact"},
            )
        )
    semantic_edges.extend(link_doc_mentions(tuple(artifacts), nodes))
    edges.extend(_dedupe_edges(semantic_edges))


def _dedupe_edges(edges: list[HarnessEdge]) -> tuple[HarnessEdge, ...]:
    seen: set[tuple[str, str, str]] = set()
    out: list[HarnessEdge] = []
    for edge in edges:
        key = (edge.source_id, edge.target_id, edge.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(edge)
    return tuple(out)


__all__ = ["add_doc_semantics"]
