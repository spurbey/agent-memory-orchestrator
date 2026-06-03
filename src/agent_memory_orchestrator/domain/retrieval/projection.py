from __future__ import annotations

import json
from typing import Any, Iterable, Protocol

from .models import RetrievalDocument


class GraphNodeSource(Protocol):
    def list_nodes(
        self,
        *,
        limit: int = 1000,
        kinds: list[str] | None = None,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        ...


SESSION_RETRIEVAL_NODE_KINDS = (
    "ReasoningNode",
    "DecisionUnit",
    "DecisionThread",
    "WorkChange",
    "GitCommit",
    "Commit",
    "CodeNode",
    "CodeImpactSummary",
    "CodeRegionRef",
    "FileImpactSummary",
    "FileRef",
    "Symbol",
    "SymbolRef",
    "SymbolVersion",
    "EvidenceRef",
    "Evidence",
)
CENTRAL_RETRIEVAL_NODE_KINDS = (
    "KnowledgeVersion",
    "KnowledgeAtom",
    "GraphCommit",
    "GraphView",
)
DEFAULT_RETRIEVAL_NODE_KINDS = SESSION_RETRIEVAL_NODE_KINDS + CENTRAL_RETRIEVAL_NODE_KINDS


def build_retrieval_documents_from_graph(
    graph_store: GraphNodeSource,
    *,
    session_id: str = "",
    kinds: list[str] | None = None,
    node_limit: int = 10000,
    max_doc_chars: int = 5000,
    pipeline_version: str = "",
    graph_schema_version: str = "",
    repo_id: str = "",
) -> list[RetrievalDocument]:
    docs: list[RetrievalDocument] = []

    if kinds is not None:
        return _documents_for_nodes(
            graph_store.list_nodes(limit=node_limit, kinds=kinds, session_id=session_id),
            max_doc_chars=max_doc_chars,
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
            repo_id=repo_id,
        )

    active_graph_commit_ids = _active_graph_commit_ids(graph_store, repo_id=repo_id)
    if active_graph_commit_ids:
        central_docs = _documents_for_nodes(
            (
                node
                for node in graph_store.list_nodes(
                    limit=node_limit,
                    kinds=list(CENTRAL_RETRIEVAL_NODE_KINDS),
                    session_id=session_id,
                )
                if _is_active_central_node(node, active_graph_commit_ids, repo_id=repo_id)
            ),
            max_doc_chars=max_doc_chars,
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
            repo_id=repo_id,
        )
        if central_docs:
            docs.extend(central_docs)

    docs.extend(
        _documents_for_nodes(
            graph_store.list_nodes(
                limit=max(node_limit, 1),
                kinds=list(SESSION_RETRIEVAL_NODE_KINDS),
                session_id=session_id,
            ),
            max_doc_chars=max_doc_chars,
            pipeline_version=pipeline_version,
            graph_schema_version=graph_schema_version,
            repo_id=repo_id,
        )
    )
    return docs


def _documents_for_nodes(
    nodes: Iterable[dict[str, Any]],
    *,
    max_doc_chars: int,
    pipeline_version: str = "",
    graph_schema_version: str = "",
    repo_id: str = "",
) -> list[RetrievalDocument]:
    docs: list[RetrievalDocument] = []
    for node in nodes:
        if pipeline_version and _node_version_value(node, "pipeline_version") != pipeline_version:
            continue
        if graph_schema_version and _node_version_value(node, "graph_schema_version") != graph_schema_version:
            continue
        if repo_id and _node_repo_id(node) != repo_id:
            continue
        docs.extend(_documents_for_node(node, max_doc_chars=max_doc_chars))
    return docs


def _active_graph_commit_ids(graph_store: GraphNodeSource, *, repo_id: str = "") -> set[str]:
    safe_repo_id = str(repo_id or "").strip()
    commit_ids: set[str] = set()
    for node in graph_store.list_nodes(limit=100, kinds=["GraphView"]):
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        branch = str(metadata.get("branch") or "")
        mode = str(metadata.get("mode") or "")
        status = str(node.get("status") or metadata.get("status") or "")
        node_repo_id = _node_repo_id(node)
        if safe_repo_id and node_repo_id != safe_repo_id:
            continue
        if branch == "main" and mode == "active" and status == "active" and metadata.get("graph_commit_id"):
            commit_ids.add(str(metadata.get("graph_commit_id") or ""))
    return commit_ids


def _is_active_central_node(node: dict[str, Any], active_graph_commit_ids: set[str], *, repo_id: str = "") -> bool:
    node_kind = str(node.get("kind") or "")
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    graph_commit_id = str(metadata.get("graph_commit_id") or "")
    safe_repo_id = str(repo_id or "").strip()
    if safe_repo_id and _node_repo_id(node) != safe_repo_id:
        return False
    if node_kind == "GraphView":
        return (
            str(metadata.get("branch") or "") == "main"
            and str(metadata.get("mode") or "") == "active"
            and (not active_graph_commit_ids or graph_commit_id in active_graph_commit_ids)
        )
    if node_kind == "GraphCommit":
        return str(node.get("id") or "") in active_graph_commit_ids or graph_commit_id in active_graph_commit_ids
    if node_kind in {"KnowledgeAtom", "KnowledgeVersion"}:
        # GraphView HEAD identifies the active branch snapshot, not only the
        # latest GraphCommit's newly-created versions. Older active versions
        # remain part of the branch until a later STATUS_CHANGED edge refines,
        # supersedes, contests, or reverts them.
        return bool(graph_commit_id) and str(node.get("status") or metadata.get("status") or "active") == "active"
    return False



def _documents_for_node(node: dict[str, Any], *, max_doc_chars: int) -> list[RetrievalDocument]:
    node_id = str(node.get("id") or "")
    node_kind = str(node.get("kind") or "GraphNode")
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    doc_type = _doc_type(node_kind)
    memory_class = _memory_class(doc_type, node_kind)
    packet_id = str(metadata.get("packet_id") or metadata.get("source_packet_id") or node.get("packet_id") or "")
    repo_id = _node_repo_id(node)
    commit_sha = str(
        metadata.get("commit_sha")
        or metadata.get("source_commit_sha")
        or node.get("commit_sha")
        or node.get("commit_id")
        or ""
    )
    title = _node_title(node)
    body = _node_body(node)
    chunks = _chunk_text(body, max_doc_chars=max_doc_chars)
    out: list[RetrievalDocument] = []
    for index, chunk in enumerate(chunks, start=1):
        suffix = f":{index}" if len(chunks) > 1 else ""
        out.append(
            RetrievalDocument(
                doc_id=f"doc:{doc_type}:{node_id}{suffix}",
                doc_type=doc_type,
                graph_node_id=node_id,
                node_kind=node_kind,
                packet_id=packet_id,
                commit_sha=commit_sha,
                title=title,
                body=chunk,
                repo_id=repo_id,
                chunk_index=index,
                chunk_count=len(chunks),
                memory_class=memory_class,
                importance=_importance(doc_type, node_kind, metadata),
                metadata={"repo_id": repo_id, "node_metadata": retrieval_metadata(metadata), "chunked": len(chunks) > 1},
            )
        )
    return out


def retrieval_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keep = {
        "atom_id",
        "atom_kind",
        "branch",
        "canonical_key",
        "canonical_key_version",
        "packet_id",
        "source_packet_id",
        "commit_sha",
        "source_commit_sha",
        "node_type",
        "subject",
        "statement",
        "reason",
        "file_path",
        "symbol",
        "symbol_id",
        "changed_files",
        "paths",
        "path",
        "qualified_name",
        "symbol_kind",
        "selected_files",
        "selected_file_roles",
        "impact_roles",
        "impact_role",
        "primary_impact_role",
        "impact_role_counts",
        "selected_symbol_refs",
        "selected_code_refs",
        "reasoning_statements",
        "promotion_grade",
        "policy",
        "original_code_node_id",
        "hunk_count",
        "evidence_refs",
        "version_count",
        "graph_commit_id",
        "idempotency_key",
        "job_id",
        "merge_plan_id",
        "mode",
        "repo_id",
        "source_node_ids",
        "status",
        "pipeline_version",
        "graph_schema_version",
        "version_metadata",
    }
    return {key: metadata[key] for key in keep if key in metadata}


def _node_version_value(node: dict[str, Any], key: str) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get(key) or metadata.get(key) or "")


def _node_repo_id(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    return str(node.get("repo_id") or metadata.get("repo_id") or "")



def _node_body(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    if str(node.get("kind") or "") == "KnowledgeVersion":
        return _central_version_body(node, metadata)
    if str(node.get("kind") or "") == "KnowledgeAtom":
        return _central_atom_body(node, metadata)
    fields = [
        f"kind: {node.get('kind') or ''}",
        f"status: {node.get('status') or ''}",
        f"summary: {node.get('summary') or ''}",
        f"commit: {node.get('commit_id') or metadata.get('commit_sha') or ''}",
        f"packet: {metadata.get('packet_id') or metadata.get('source_packet_id') or ''}",
    ]
    for key in (
        "atom_kind",
        "canonical_key",
        "atom_id",
        "graph_commit_id",
        "merge_plan_id",
        "repo_id",
        "source_node_ids",
        "version_metadata",
        "selected_file_roles",
        "impact_roles",
        "impact_role",
        "primary_impact_role",
    ):
        if key in metadata:
            value = metadata[key]
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, sort_keys=True)
            fields.append(f"{key}: {value}")
    for key in (
        "node_type",
        "subject",
        "statement",
        "reason",
        "file_path",
        "symbol",
        "symbol_id",
        "changed_files",
        "paths",
        "evidence_refs",
    ):
        if key in metadata:
            value = metadata[key]
            if isinstance(value, (dict, list, tuple)):
                value = json.dumps(value, sort_keys=True)
            fields.append(f"{key}: {value}")
    fields.append("metadata: " + json.dumps(metadata, sort_keys=True))
    return "\n".join(str(field) for field in fields if str(field).strip())


def _node_title(node: dict[str, Any]) -> str:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    kind = str(node.get("kind") or "")
    if kind == "KnowledgeVersion":
        version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
        atom_kind = str(metadata.get("atom_kind") or "")
        if atom_kind in {"decision", "problem"}:
            subject = str(version_metadata.get("subject") or version_metadata.get("summary") or version_metadata.get("statement") or "").strip()
            return f"{atom_kind.title()}: {subject}" if subject else f"{atom_kind.title()} version"
        if atom_kind == "file":
            path = _central_file_path(metadata, version_metadata)
            return f"File version: {path}" if path else "File version"
        if atom_kind == "commit":
            sha = _central_commit_sha(metadata, version_metadata)
            return f"Commit version: {sha[:12]}" if sha else "Commit version"
        return f"{atom_kind.title() or 'Knowledge'} version"
    if kind == "KnowledgeAtom":
        atom_kind = str(metadata.get("atom_kind") or "")
        canonical_key = str(metadata.get("canonical_key") or "")
        return f"{atom_kind.title() or 'Knowledge'} atom: {canonical_key.rsplit('|', 1)[-1]}"
    return str(node.get("label") or node.get("id") or "")


def _central_version_body(node: dict[str, Any], metadata: dict[str, Any]) -> str:
    version_metadata = metadata.get("version_metadata") if isinstance(metadata.get("version_metadata"), dict) else {}
    atom_kind = str(metadata.get("atom_kind") or "")
    fields = [
        "kind: KnowledgeVersion",
        f"atom_kind: {atom_kind}",
        f"status: {node.get('status') or metadata.get('status') or ''}",
    ]
    if atom_kind in {"decision", "problem"}:
        fields.extend(
            [
                f"subject: {version_metadata.get('subject') or ''}",
                f"summary: {version_metadata.get('summary') or ''}",
                f"statement: {version_metadata.get('statement') or ''}",
                f"rationale: {version_metadata.get('rationale') or ''}",
                "linked_files: " + ", ".join(_string_values(version_metadata.get("linked_files"))[:8]),
                "linked_commits: " + ", ".join(_string_values(version_metadata.get("linked_commits"))[:6]),
                "source: active central memory",
            ]
        )
    elif atom_kind == "file":
        fields.extend(
            [
                f"file_path: {_central_file_path(metadata, version_metadata)}",
                f"producing_commit_sha: {version_metadata.get('producing_commit_sha') or ''}",
                "source: active central file history",
            ]
        )
    elif atom_kind == "commit":
        fields.extend(
            [
                f"commit_sha: {_central_commit_sha(metadata, version_metadata)}",
                "source: active central commit history",
            ]
        )
    else:
        fields.extend(
            [
                f"summary: {node.get('summary') or ''}",
                f"canonical_key: {metadata.get('canonical_key') or version_metadata.get('canonical_key') or ''}",
                f"file_path: {version_metadata.get('file_path') or ''}",
                f"qualified_name: {version_metadata.get('qualified_name') or version_metadata.get('symbol') or ''}",
                "version_metadata: " + json.dumps(version_metadata, sort_keys=True),
                "source: active central code history",
            ]
        )
    return "\n".join(field for field in fields if field and not field.endswith(": "))


def _central_atom_body(node: dict[str, Any], metadata: dict[str, Any]) -> str:
    atom_kind = str(metadata.get("atom_kind") or "")
    fields = [
        "kind: KnowledgeAtom",
        f"atom_kind: {atom_kind}",
        f"status: {node.get('status') or metadata.get('status') or ''}",
        f"canonical_key: {metadata.get('canonical_key') or ''}",
        "source: central canonical identity",
    ]
    return "\n".join(field for field in fields if field and not field.endswith(": "))


def _central_file_path(metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    canonical_key = str(version_metadata.get("canonical_key") or metadata.get("canonical_key") or "")
    if "|file|" in canonical_key:
        return canonical_key.rsplit("|", 1)[-1]
    if canonical_key.startswith("file|"):
        parts = canonical_key.split("|", 2)
        return parts[-1] if len(parts) == 3 else ""
    return str(version_metadata.get("file_path") or "")


def _central_commit_sha(metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    canonical_key = str(version_metadata.get("canonical_key") or metadata.get("canonical_key") or "")
    if "|commit|" in canonical_key:
        return canonical_key.rsplit("|", 1)[-1]
    if canonical_key.startswith("commit|"):
        parts = canonical_key.split("|", 2)
        return parts[-1] if len(parts) == 3 else ""
    return str(version_metadata.get("commit_sha") or "")


def _string_values(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    if value is None or not str(value).strip():
        return []
    return [str(value).strip()]


def _chunk_text(text: str, *, max_doc_chars: int) -> list[str]:
    if len(text) <= max_doc_chars:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > max_doc_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if line_len > max_doc_chars:
            for start in range(0, len(line), max_doc_chars):
                part = line[start : start + max_doc_chars]
                if part:
                    chunks.append(part)
            continue
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [text[:max_doc_chars]]



def _doc_type(node_kind: str) -> str:
    if node_kind == "KnowledgeVersion":
        return "central_version"
    if node_kind == "KnowledgeAtom":
        return "central_atom"
    if node_kind in {"GraphCommit", "GraphView"}:
        return "graph_lineage"
    if node_kind in {"ReasoningNode", "DecisionUnit", "DecisionThread"}:
        return "reasoning"
    if node_kind in {"WorkChange", "GitCommit", "Commit"}:
        return "commit"
    if node_kind in {"CodeNode", "CodeHunk"}:
        return "code"
    if node_kind == "CodeImpactSummary":
        return "code_impact"
    if node_kind == "FileImpactSummary":
        return "file_impact"
    if node_kind == "FileRef":
        return "file_ref"
    if node_kind == "CodeRegionRef":
        return "code_region_ref"
    if node_kind == "SymbolRef":
        return "symbol_ref"
    if node_kind in {"Symbol", "SymbolVersion"}:
        return "symbol"
    if node_kind in {"EvidenceRef", "Evidence", "ToolFact"}:
        return "evidence"
    return "graph"


def _memory_class(doc_type: str, node_kind: str) -> str:
    if doc_type == "central_version":
        return "central_active_memory"
    if doc_type == "central_atom":
        return "central_canonical_atom"
    if doc_type == "graph_lineage":
        return "graph_lineage"
    if doc_type == "reasoning":
        return "answer_grade_reasoning"
    if doc_type == "code_impact":
        return "code_impact_summary"
    if doc_type == "file_impact":
        return "file_impact_summary"
    if doc_type in {"file_ref", "symbol_ref", "code_region_ref"}:
        return "code_support"
    if doc_type == "code":
        return "code_change"
    if doc_type == "symbol":
        return "symbol_version"
    if doc_type == "commit" or node_kind == "WorkChange":
        return "work_change"
    if doc_type == "evidence":
        return "supporting_evidence"
    return "graph_context"


def _importance(doc_type: str, node_kind: str, metadata: dict[str, Any]) -> float:
    if isinstance(metadata.get("importance"), (int, float)):
        return float(metadata["importance"])
    role = str(metadata.get("impact_role") or metadata.get("primary_impact_role") or "")
    if doc_type == "central_version":
        return 0.95
    if doc_type == "central_atom":
        return 0.75
    if doc_type == "graph_lineage":
        return 0.25
    if doc_type == "reasoning":
        return 0.9
    if doc_type == "code_impact":
        return 0.82
    if doc_type == "file_impact":
        if role == "validation_test":
            return 0.55
        if role in {"docs", "config"}:
            return 0.62
        return 0.84
    if node_kind == "WorkChange" or doc_type == "commit":
        return 0.8
    if doc_type in {"file_ref", "symbol_ref", "code_region_ref"}:
        if role == "validation_test":
            return 0.42
        if role in {"docs", "config"}:
            return 0.50
        if role in {"ui_style", "ui_markup"}:
            return 0.56
        return 0.62
    if doc_type == "code":
        return 0.7
    if doc_type == "symbol":
        return 0.65
    if doc_type == "evidence":
        return 0.35
    return 0.5




__all__ = [
    "CENTRAL_RETRIEVAL_NODE_KINDS",
    "DEFAULT_RETRIEVAL_NODE_KINDS",
    "SESSION_RETRIEVAL_NODE_KINDS",
    "build_retrieval_documents_from_graph",
    "retrieval_metadata",
]
