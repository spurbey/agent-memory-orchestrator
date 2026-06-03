from __future__ import annotations

import hashlib
import json
from dataclasses import replace as dataclass_replace
from pathlib import Path
from typing import Any

from ...domain.pipeline.constants import GRAPH_SCHEMA_VERSION
from ...domain.pipeline.constants import PIPELINE_VERSION
from ...domain.retrieval.models import RetrievalDocument
from .stage_artifacts import _read_json


def _retrieval_documents_from_manifest(
    *,
    manifest_path: Path,
    source: str,
    job: dict[str, Any],
    repo_id: str,
    max_doc_chars: int,
    limit: int,
) -> list[RetrievalDocument]:
    manifest = _read_json(manifest_path)
    nodes = manifest.get("nodes") if isinstance(manifest, dict) else []
    namespace = str(job["job_id"]).rsplit(":", 1)[-1][:12]
    docs: list[RetrievalDocument] = []
    for index, node in enumerate(nodes if isinstance(nodes, list) else []):
        if limit and index >= limit:
            break
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        kind = str(node.get("kind") or "")
        if not node_id or not kind:
            continue
        metadata = _safe_json_object(str(node.get("properties_json") or "{}"))
        doc_type, memory_class, importance = _fallback_doc_profile(kind)
        metadata.update(
            {
                "repo_id": repo_id,
                "pipeline_version": PIPELINE_VERSION,
                "graph_schema_version": GRAPH_SCHEMA_VERSION,
                "source": source,
                "job_id": job.get("job_id"),
                "session_id": job.get("session_id"),
                "original_node_id": node_id,
            }
        )
        title = str(node.get("label") or node_id)
        summary = str(node.get("summary") or "")
        body = "\n".join(
            part
            for part in (
                f"{kind}: {title}",
                summary,
                json.dumps(_retrieval_fallback_metadata(metadata), ensure_ascii=False, sort_keys=True),
            )
            if part
        )
        docs.append(
            RetrievalDocument(
                doc_id=f"retrieval:{repo_id}:{namespace}:{node_id}:1",
                doc_type=doc_type,
                graph_node_id=f"{namespace}:{node_id}",
                node_kind=kind,
                packet_id=str(node.get("packet_id") or metadata.get("packet_id") or metadata.get("source_packet_id") or ""),
                commit_sha=str(node.get("commit_sha") or metadata.get("commit_sha") or metadata.get("source_commit_sha") or ""),
                title=title,
                body=_clip_text(body, max_doc_chars),
                repo_id=repo_id,
                memory_class=memory_class,
                importance=importance,
                metadata=metadata,
            )
        )
    return docs


def _merge_cumulative_retrieval_docs(
    *,
    existing_docs: list[RetrievalDocument],
    current_docs: list[RetrievalDocument],
) -> list[RetrievalDocument]:
    """Build the active repo projection as a cumulative product-memory surface."""
    merged: dict[str, RetrievalDocument] = {}
    current_has_central_docs = any(str(doc.metadata.get("source") or "") == "central_active_graph_view" for doc in current_docs)
    for doc in existing_docs:
        source = str(doc.metadata.get("source") or "")
        if source not in {"curated_graph_manifest", "central_active_graph_view"}:
            continue
        if current_has_central_docs and source == "central_active_graph_view":
            continue
        if doc.node_kind in {"CodeNode", "CodeHunk", "Symbol", "CodeVersion"}:
            continue
        merged[doc.doc_id] = dataclass_replace(
            doc,
            metadata={key: value for key, value in doc.metadata.items() if key != "projection_id"},
        )
    for doc in current_docs:
        merged[doc.doc_id] = dataclass_replace(
            doc,
            metadata={key: value for key, value in doc.metadata.items() if key != "projection_id"},
        )
    return sorted(
        merged.values(),
        key=lambda doc: (doc.metadata.get("job_id", ""), doc.doc_type, doc.graph_node_id, doc.chunk_index, doc.doc_id),
    )


def _retrieval_projection_id(*, repo_id: str, projection_version: str, source_artifact_hash: str, doc_content_hash: str) -> str:
    payload = {
        "repo_id": repo_id,
        "projection_version": projection_version,
        "source_artifact_hash": source_artifact_hash,
        "doc_content_hash": doc_content_hash,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:32]
    return f"rproj:{digest}"


def _retrieval_doc_content_hash(docs: list[RetrievalDocument]) -> str:
    payload = [
        {
            "doc_id": doc.doc_id,
            "doc_type": doc.doc_type,
            "graph_node_id": doc.graph_node_id,
            "node_kind": doc.node_kind,
            "packet_id": doc.packet_id,
            "commit_sha": doc.commit_sha,
            "title": doc.title,
            "body": doc.body,
            "memory_class": doc.memory_class,
            "importance": doc.importance,
            "metadata": {key: value for key, value in doc.metadata.items() if key != "projection_id"},
        }
        for doc in docs
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _retrieval_projection_activation_gate(docs: list[RetrievalDocument]) -> dict[str, Any]:
    raw_trace_docs = [
        doc
        for doc in docs
        if doc.node_kind in {"CodeNode", "CodeHunk", "Symbol", "CodeVersion"}
        or doc.doc_type in {"session_codenode", "session_codehunk", "session_symbol", "code"}
    ]
    product_docs = [
        doc
        for doc in docs
        if doc.doc_type
        in {
            "central_atom",
            "central_version",
            "code_impact",
            "file_impact",
            "reasoning",
            "commit",
            "packet",
            "file_ref",
            "symbol_ref",
            "code_region_ref",
        }
    ]
    failures: list[str] = []
    if not docs:
        failures.append("retrieval_projection_no_docs")
    if raw_trace_docs:
        failures.append("retrieval_projection_contains_raw_trace_docs")
    if not product_docs:
        failures.append("retrieval_projection_missing_product_docs")
    return {
        "passed": not failures,
        "blocking_failures": failures,
        "summary": {
            "doc_count": len(docs),
            "product_doc_count": len(product_docs),
            "raw_trace_doc_count": len(raw_trace_docs),
            "raw_trace_examples": [
                {"doc_id": doc.doc_id, "doc_type": doc.doc_type, "node_kind": doc.node_kind, "title": doc.title}
                for doc in raw_trace_docs[:5]
            ],
        },
    }


def _clip_text(text: str, limit: int) -> str:
    safe_limit = max(256, int(limit or 0))
    if len(text) <= safe_limit:
        return text
    return text[: max(0, safe_limit - 18)].rstrip() + "\n... <clipped>"


def _safe_json_object(raw: str) -> dict[str, Any]:
    try:
        loaded = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _retrieval_fallback_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    keep = (
        "packet_id",
        "source_packet_id",
        "commit_sha",
        "source_commit_sha",
        "path",
        "qualified_name",
        "symbol_kind",
        "node_type",
        "subject",
        "statement",
        "summary",
        "selected_files",
        "selected_file_roles",
        "impact_roles",
        "impact_role",
        "primary_impact_role",
        "impact_role_counts",
        "selected_symbol_refs",
        "selected_code_refs",
        "reasoning_statements",
        "impact_count",
        "packet_ids",
        "commit_shas",
        "impact_ids",
        "reasons",
        "commit_messages",
        "promotion_grade",
        "policy",
        "hunk_count",
        "original_code_node_id",
        "evidence_refs",
        "repo_id",
        "job_id",
        "session_id",
    )
    return {key: metadata.get(key) for key in keep if metadata.get(key)}


def _fallback_doc_profile(kind: str) -> tuple[str, str, float]:
    if kind == "ReasoningNode":
        return "reasoning", "answer_grade_reasoning", 0.9
    if kind == "CodeImpactSummary":
        return "code_impact", "code_impact_summary", 0.82
    if kind == "FileImpactSummary":
        return "file_impact", "file_impact_summary", 0.84
    if kind == "Commit":
        return "commit", "work_change", 0.72
    if kind == "EvidenceRef":
        return "evidence", "supporting_evidence", 0.35
    if kind == "Packet":
        return "packet", "work_packet", 0.5
    if kind == "FileRef":
        return "file_ref", "code_support", 0.62
    if kind == "SymbolRef":
        return "symbol_ref", "code_support", 0.6
    if kind == "CodeRegionRef":
        return "code_region_ref", "code_support", 0.58
    return f"session_{kind.lower()}", "support", 0.5
