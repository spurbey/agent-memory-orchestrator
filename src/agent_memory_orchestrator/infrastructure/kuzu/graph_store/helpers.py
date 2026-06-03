from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from .models import GraphNode


SEARCH_STOPWORDS = {
    "about",
    "after",
    "again",
    "also",
    "and",
    "are",
    "because",
    "been",
    "before",
    "being",
    "between",
    "but",
    "can",
    "could",
    "did",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "into",
    "its",
    "not",
    "now",
    "only",
    "should",
    "that",
    "the",
    "then",
    "this",
    "use",
    "used",
    "using",
    "via",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "why",
    "will",
    "with",
    "would",
}

def _node_props(node: GraphNode) -> str:
    return ", ".join(
        [
            f"id: {_q(node.id)}",
            f"kind: {_q(node.kind)}",
            f"label: {_q(node.label)}",
            f"summary: {_q(node.summary)}",
            f"status: {_q(node.status)}",
            f"scope: {_q(node.scope)}",
            f"session_id: {_q(node.session_id)}",
            f"project_id: {_q(node.project_id)}",
            f"source_app: {_q(node.source_app)}",
            f"evidence_id: {_q(node.evidence_id)}",
            f"commit_id: {_q(node.commit_id)}",
            f"created_at: {_q(node.created_at)}",
            f"updated_at: {_q(node.updated_at)}",
            f"metadata_json: {_q(json.dumps(node.metadata, sort_keys=True))}",
        ]
    )


def _search_where(query: str, kinds: list[str] | None) -> str:
    clauses: list[str] = []
    terms = _terms(query)
    if terms:
        clauses.append(
            "("
            + " OR ".join(
                f"lower(n.label) CONTAINS {_q(term)} OR lower(n.summary) CONTAINS {_q(term)} "
                f"OR lower(n.metadata_json) CONTAINS {_q(term)}"
                for term in terms[:8]
            )
            + ")"
        )
    if kinds:
        clauses.append("(" + " OR ".join(f"n.kind = {_q(kind)}" for kind in kinds) + ")")
    return "WHERE " + " AND ".join(clauses) if clauses else ""


def _compact_search_where(query: str, kinds: list[str] | None) -> str:
    clauses: list[str] = []
    terms = _terms(query)
    if terms:
        clauses.append(
            "("
            + " OR ".join(
                f"lower(n.label) CONTAINS {_q(term)} OR lower(n.summary) CONTAINS {_q(term)} "
                f"OR lower(n.properties_json) CONTAINS {_q(term)}"
                for term in terms[:8]
            )
            + ")"
        )
    if kinds:
        clauses.append("(" + " OR ".join(f"n.kind = {_q(kind)}" for kind in kinds) + ")")
    return "WHERE " + " AND ".join(clauses) if clauses else ""


def _rows(result: Any) -> list[list[Any]]:
    rows: list[list[Any]] = []
    if hasattr(result, "has_next") and hasattr(result, "get_next"):
        while result.has_next():
            rows.append(result.get_next())
        return rows
    if isinstance(result, list):
        return result
    return rows


def _row_to_node(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[13] if len(row) > 13 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row[0],
        "kind": row[1],
        "label": row[2],
        "summary": row[3],
        "status": row[4],
        "scope": row[5],
        "session_id": row[6],
        "project_id": row[7],
        "source_app": row[8],
        "evidence_id": row[9],
        "commit_id": row[10],
        "created_at": row[11],
        "updated_at": row[12],
        "metadata": metadata,
    }


def _compact_row_to_node(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[6] if len(row) > 6 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    status = str(metadata.get("status") or metadata.get("node_status") or "session_final")
    session_id = str(metadata.get("session_id") or metadata.get("source_session_id") or "")
    evidence_refs = metadata.get("evidence_refs") if isinstance(metadata.get("evidence_refs"), list) else []
    evidence_id = str(evidence_refs[0]) if evidence_refs else str(metadata.get("evidence_id") or "")
    return {
        "id": row[0],
        "kind": row[1],
        "label": row[4],
        "summary": row[5],
        "status": status,
        "scope": str(metadata.get("scope") or "session"),
        "session_id": session_id,
        "project_id": str(metadata.get("project_id") or "default"),
        "source_app": str(metadata.get("source_app") or "unknown"),
        "evidence_id": evidence_id,
        "commit_id": row[3],
        "created_at": str(metadata.get("created_at") or ""),
        "updated_at": str(metadata.get("updated_at") or ""),
        "packet_id": row[2],
        "commit_sha": row[3],
        "metadata": {
            **metadata,
            "packet_id": metadata.get("packet_id") or row[2],
            "commit_sha": metadata.get("commit_sha") or row[3],
        },
    }


def _row_to_edge(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[8] if len(row) > 8 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    return {
        "id": row[0],
        "source_id": row[1],
        "target_id": row[2],
        "kind": row[3],
        "weight": float(row[4] or 0.0),
        "confidence": float(row[5] or 0.0),
        "evidence_id": row[6],
        "created_at": row[7],
        "metadata": metadata,
    }


def _compact_row_to_edge(row: list[Any]) -> dict[str, Any]:
    metadata_raw = row[3] if len(row) > 3 else "{}"
    try:
        metadata = json.loads(metadata_raw or "{}")
    except json.JSONDecodeError:
        metadata = {}
    source_id = str(row[0])
    target_id = str(row[1])
    kind = str(row[2])
    return {
        "id": str(metadata.get("id") or f"edge:{source_id}:{kind}:{target_id}"),
        "source_id": source_id,
        "target_id": target_id,
        "kind": kind,
        "weight": float(metadata.get("weight") or 1.0),
        "confidence": float(metadata.get("confidence") or 0.8),
        "evidence_id": str(metadata.get("evidence_id") or ""),
        "created_at": str(metadata.get("created_at") or ""),
        "metadata": metadata,
    }


def _q(value: object) -> str:
    return json.dumps("" if value is None else str(value))


def _kuzu_buffer_pool_size() -> int:
    raw = os.environ.get("AMO_KUZU_GRAPH_BUFFER_POOL_SIZE", "").strip()
    if raw:
        try:
            return max(256 * 1024 * 1024, int(raw))
        except ValueError:
            pass
    return 0


def _kuzu_max_threads() -> int:
    raw = os.environ.get("AMO_KUZU_MAX_THREADS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 2


def _terms(text: str) -> list[str]:
    terms: list[str] = []
    for token in str(text or "").split():
        clean = re.sub(r"[^a-z0-9_.-]+", "", token.lower())
        if len(clean) <= 2:
            continue
        if clean in SEARCH_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", clean):
            continue
        terms.append(clean)
    return terms


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
