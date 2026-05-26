from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .store import GraphStore
from .store import SEARCH_STOPWORDS


TRACE_EDGE_PRIORITIES = {
    "REASON_NODE_IN_PACKET": 1.0,
    "REASON_NODE_EXPLAINS_COMMIT": 1.0,
    "REASON_NODE_EVIDENCED_BY": 1.0,
    "REASON_NODE_VALIDATED_BY": 0.7,
    "REASON_NODE_LINKED_TO_CODE_NODE": 0.95,
    "REASON_NODE_LINKED_TO_CODE_VERSION": 0.85,
    "REASON_NODE_LINKED_TO_SYMBOL": 0.85,
    "REASON_NODE_LINKED_TO_HUNK": 0.9,
    "COMMIT_PRODUCED_HUNK": 0.75,
    "COMMIT_HAS_CODE_VERSION": 0.7,
    "COMMIT_VALIDATED_BY": 0.65,
    "HUNK_MAPS_TO_CODE_NODE": 0.75,
    "VERSION_CONTAINS_CODE_NODE": 0.65,
    "SYMBOL_HAS_VERSION": 0.65,
    "CODE_VERSION_OF_SYMBOL": 0.65,
    "VERSION_SUPERSEDED_BY": 0.45,
}

TRACE_KIND_PRIORITIES = {
    "ReasoningNode": 1.0,
    "DecisionUnit": 1.0,
    "DecisionThread": 1.0,
    "CodeNode": 0.9,
    "CodeHunk": 0.85,
    "Symbol": 0.8,
    "SymbolVersion": 0.75,
    "CodeVersion": 0.75,
    "Commit": 0.7,
    "GitCommit": 0.7,
    "WorkChange": 0.7,
    "EvidenceRef": 0.65,
    "Evidence": 0.65,
    "Packet": 0.6,
    "TestRun": 0.55,
}

CHAIN_ROLES = ("Problem", "Cause", "Decision", "Constraint", "Fix", "OpenQuestion")


@dataclass(slots=True)
class _TraceCandidate:
    node_id: str
    distance: int
    score: float
    path: list[dict[str, str]]


def build_answer_trace(
    *,
    seed_node_id: str,
    graph_store: GraphStore,
    query: str = "",
    session_id: str = "",
    max_depth: int = 3,
    node_limit: int = 80,
    edge_limit: int = 200000,
) -> dict[str, Any]:
    """Build a typed answer trace around one retrieved graph node.

    Retrieval finds the best door. This trace follows graph-safe relationship
    types from that door to packet, commit, evidence, hunk, code and sibling
    reasoning nodes so answers can say what happened and why it happened.
    """

    seed_node_id = str(seed_node_id or "").strip()
    if not seed_node_id:
        return _empty_trace(seed_node_id)

    nodes = {
        str(node.get("id") or ""): node
        for node in graph_store.list_nodes(limit=max(1000, edge_limit // 2), session_id=session_id)
        if str(node.get("id") or "")
    }
    if seed_node_id not in nodes:
        return _empty_trace(seed_node_id)

    edges = [
        edge
        for edge in graph_store.list_edges(limit=edge_limit, session_id=session_id)
        if str(edge.get("kind") or "") in TRACE_EDGE_PRIORITIES
    ]
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        source_id = str(edge.get("source_id") or "")
        target_id = str(edge.get("target_id") or "")
        if not source_id or not target_id:
            continue
        adjacency.setdefault(source_id, []).append({**edge, "other_id": target_id, "direction": "out"})
        adjacency.setdefault(target_id, []).append({**edge, "other_id": source_id, "direction": "in"})

    query_terms = _terms(query)
    candidates: dict[str, _TraceCandidate] = {
        seed_node_id: _TraceCandidate(seed_node_id, 0, 10.0 + _node_score(nodes[seed_node_id], query_terms), [])
    }
    queue: list[_TraceCandidate] = [candidates[seed_node_id]]
    visited_depth = {seed_node_id: 0}

    while queue and len(candidates) < node_limit * 6:
        current = queue.pop(0)
        if current.distance >= max_depth:
            continue
        relations = sorted(
            adjacency.get(current.node_id, []),
            key=lambda edge: _edge_sort_key(edge, nodes.get(str(edge.get("other_id") or ""), {}), query_terms),
            reverse=True,
        )
        for edge in relations[:80]:
            other_id = str(edge.get("other_id") or "")
            if other_id not in nodes:
                continue
            next_depth = current.distance + 1
            if visited_depth.get(other_id, 999) <= next_depth:
                continue
            edge_kind = str(edge.get("kind") or "")
            next_node = nodes[other_id]
            step = {
                "from": current.node_id,
                "edge_kind": edge_kind,
                "to": other_id,
                "direction": str(edge.get("direction") or ""),
            }
            score = (
                TRACE_EDGE_PRIORITIES.get(edge_kind, 0.0)
                + _node_score(next_node, query_terms)
                + TRACE_KIND_PRIORITIES.get(str(next_node.get("kind") or ""), 0.2)
                - (next_depth * 0.12)
            )
            candidate = _TraceCandidate(other_id, next_depth, score, [*current.path, step])
            candidates[other_id] = candidate
            visited_depth[other_id] = next_depth
            queue.append(candidate)

    selected = _select_trace_nodes(candidates, nodes)
    seed_node = nodes[seed_node_id]
    return {
        "seed_node_id": seed_node_id,
        "max_depth": max_depth,
        "node_count": len(selected),
        "chain": _chain_nodes(selected, nodes, seed_node=seed_node, query_terms=query_terms),
        "packets": _bucket_nodes(selected, nodes, {"Packet"}, limit=3),
        "commits": _bucket_nodes(selected, nodes, {"Commit", "GitCommit", "WorkChange"}, limit=4),
        "evidence": _bucket_nodes(selected, nodes, {"EvidenceRef", "Evidence"}, limit=5),
        "code_hunks": _bucket_nodes(selected, nodes, {"CodeHunk"}, limit=8),
        "code_nodes": _bucket_nodes(selected, nodes, {"CodeNode", "CodeVersion"}, limit=8),
        "symbols": _bucket_nodes(selected, nodes, {"Symbol", "SymbolVersion"}, limit=8),
        "paths": _selected_paths(selected, limit=16),
        "support": _trace_support(selected, nodes, seed_node=seed_node),
    }


def format_answer_trace(trace: dict[str, Any]) -> str:
    if not trace or not trace.get("node_count"):
        return ""
    chain_parts: list[str] = []
    for item in trace.get("chain") or []:
        role = str(item.get("role") or "").strip()
        summary = _short_node_text(item)
        if role and summary:
            chain_parts.append(f"{role}: {summary}")
    support = trace.get("support") if isinstance(trace.get("support"), dict) else {}
    support_parts: list[str] = []
    commits = support.get("commit_shas") or []
    evidence = support.get("evidence_ids") or []
    code = support.get("code_nodes") or []
    if commits:
        support_parts.append("commit " + ", ".join(commits[:2]))
    if evidence:
        support_parts.append("evidence " + ", ".join(evidence[:3]))
    if code:
        support_parts.append("code " + "; ".join(code[:3]))
    left = " -> ".join(chain_parts[:5])
    right = " | ".join(support_parts)
    return " | ".join(part for part in (left, right) if part)


def build_central_answer_trace(
    *,
    repo_id: str,
    graph_view: dict[str, Any] | None = None,
    graph_commit: dict[str, Any] | None = None,
    central_versions: Iterable[dict[str, Any]] = (),
    support_docs: Iterable[Any] = (),
    warnings: Iterable[str] = (),
    answer: str = "",
    status: str = "",
) -> dict[str, Any]:
    """Build the deterministic central-memory answer trace contract.

    This is intentionally structural only. Ranked retrieval can provide support
    docs, but the trust boundary is the active GraphView/GraphCommit plus
    packet/commit/file support extracted from those docs.
    """

    view = graph_view or {}
    commit = graph_commit or {}
    docs = [_doc_payload(doc) for doc in support_docs]
    version_payloads = list(central_versions)
    graph_view_id = str(view.get("view_id") or view.get("id") or "")
    graph_commit_id = str(view.get("graph_commit_id") or commit.get("graph_commit_id") or commit.get("id") or "")
    trace = {
        "repo_id": str(repo_id or view.get("repo_id") or commit.get("repo_id") or ""),
        "graph_view_id": graph_view_id,
        "graph_commit_id": graph_commit_id,
        "central_versions": _central_version_refs(version_payloads, docs),
        "packets": _unique(_doc_value(doc, "packet_id") for doc in docs),
        "commits": _unique(_doc_value(doc, "commit_sha") for doc in docs),
        "evidence_refs": _unique(_doc_metadata_values(docs, ("evidence_refs", "evidence_id", "evidence_ref_id"))),
        "files": _unique(_doc_metadata_values(docs, ("path", "file_path", "selected_files", "normalized_file_path"))),
        "code_impacts": _unique(
            [
                *(
                    doc.get("graph_node_id") or doc.get("id")
                    for doc in docs
                    if str(doc.get("doc_type") or "").lower() == "code_impact" or str(doc.get("node_kind") or "") == "CodeImpactSummary"
                ),
                *_doc_metadata_values(docs, ("impact_ids", "impact_id", "code_impact_ids", "code_impact_id")),
            ]
        ),
    }
    warning_list = _unique(warnings)
    resolved_status = status or ("active" if graph_view_id and graph_commit_id else "partial")
    if warning_list and resolved_status == "active":
        resolved_status = "review_required"
    return {
        "answer": answer,
        "status": resolved_status,
        "trace": trace,
        "support_docs": docs,
        "warnings": warning_list,
    }


def _doc_payload(doc: Any) -> dict[str, Any]:
    if isinstance(doc, dict):
        return doc
    as_dict = getattr(doc, "as_dict", None)
    if callable(as_dict):
        payload = as_dict()
        return payload if isinstance(payload, dict) else {}
    return {}


def _doc_value(doc: dict[str, Any], key: str) -> Any:
    if doc.get(key):
        return doc.get(key)
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    return metadata.get(key)


def _doc_metadata_values(docs: list[dict[str, Any]], keys: tuple[str, ...]) -> list[Any]:
    values: list[Any] = []
    for doc in docs:
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        for key in keys:
            values.extend([doc.get(key), metadata.get(key)])
    return values


def _central_version_refs(version_payloads: list[dict[str, Any]], docs: list[dict[str, Any]]) -> list[str]:
    values: list[Any] = [version.get("version_id") or version.get("id") for version in version_payloads]
    for doc in docs:
        if str(doc.get("node_kind") or "") == "KnowledgeVersion" or str(doc.get("doc_type") or "") == "central_version":
            values.append(doc.get("graph_node_id") or doc.get("id"))
    return _unique(values)


def _empty_trace(seed_node_id: str) -> dict[str, Any]:
    return {
        "seed_node_id": seed_node_id,
        "max_depth": 0,
        "node_count": 0,
        "chain": [],
        "packets": [],
        "commits": [],
        "evidence": [],
        "code_hunks": [],
        "code_nodes": [],
        "symbols": [],
        "paths": [],
        "support": {
            "packet_ids": [],
            "commit_shas": [],
            "evidence_ids": [],
            "code_node_ids": [],
            "code_nodes": [],
            "neighbor_node_ids": [],
        },
    }


def _select_trace_nodes(candidates: dict[str, _TraceCandidate], nodes: dict[str, dict[str, Any]]) -> list[_TraceCandidate]:
    grouped: dict[str, list[_TraceCandidate]] = {}
    for candidate in candidates.values():
        kind = str(nodes.get(candidate.node_id, {}).get("kind") or "GraphNode")
        grouped.setdefault(kind, []).append(candidate)

    limits = {
        "ReasoningNode": 12,
        "DecisionUnit": 8,
        "DecisionThread": 8,
        "Packet": 3,
        "Commit": 4,
        "GitCommit": 4,
        "WorkChange": 4,
        "EvidenceRef": 5,
        "Evidence": 5,
        "CodeHunk": 8,
        "CodeNode": 8,
        "CodeVersion": 6,
        "Symbol": 8,
        "SymbolVersion": 6,
        "TestRun": 3,
    }
    selected: list[_TraceCandidate] = []
    for kind, items in grouped.items():
        items.sort(key=lambda item: (item.score, -item.distance), reverse=True)
        selected.extend(items[: limits.get(kind, 3)])
    selected.sort(key=lambda item: (item.distance, -item.score, item.node_id))
    return selected[:80]


def _chain_nodes(
    selected: list[_TraceCandidate],
    nodes: dict[str, dict[str, Any]],
    *,
    seed_node: dict[str, Any],
    query_terms: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seed_packet = _packet_id(seed_node)
    seed_commit = _commit_sha(seed_node)
    for role in CHAIN_ROLES:
        role_items = [
            item
            for item in selected
            if str(nodes.get(item.node_id, {}).get("kind") or "") in {"ReasoningNode", "DecisionUnit", "DecisionThread"}
            and _reason_role(nodes.get(item.node_id, {})) == role
        ]
        if not role_items:
            continue
        scoped_items = [
            item
            for item in role_items
            if _same_scope(nodes[item.node_id], seed_packet=seed_packet, seed_commit=seed_commit)
        ]
        ranked_items = scoped_items or role_items
        ranked_items.sort(
            key=lambda item: (
                _scope_score(nodes[item.node_id], seed_packet=seed_packet, seed_commit=seed_commit),
                _visible_query_overlap(nodes[item.node_id], query_terms),
                _query_overlap(nodes[item.node_id], query_terms),
                item.score,
                -item.distance,
            ),
            reverse=True,
        )
        for item in ranked_items[:1]:
            out.append(_trace_node(item, nodes[item.node_id], role=role))
    return out


def _bucket_nodes(
    selected: list[_TraceCandidate],
    nodes: dict[str, dict[str, Any]],
    kinds: set[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    items = [item for item in selected if str(nodes.get(item.node_id, {}).get("kind") or "") in kinds]
    items.sort(key=lambda item: (item.score, -item.distance), reverse=True)
    return [_trace_node(item, nodes[item.node_id]) for item in items[:limit]]


def _selected_paths(selected: list[_TraceCandidate], *, limit: int) -> list[list[dict[str, str]]]:
    out: list[list[dict[str, str]]] = []
    seen: set[str] = set()
    for item in selected:
        if not item.path:
            continue
        key = json.dumps(item.path, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item.path)
        if len(out) >= limit:
            break
    return out


def _trace_support(
    selected: list[_TraceCandidate],
    nodes: dict[str, dict[str, Any]],
    *,
    seed_node: dict[str, Any],
) -> dict[str, list[str]]:
    seed_packet = _packet_id(seed_node)
    seed_commit = _commit_sha(seed_node)
    ordered = sorted(
        selected,
        key=lambda item: (
            _scope_score(nodes.get(item.node_id, {}), seed_packet=seed_packet, seed_commit=seed_commit),
            -item.distance,
            item.score,
        ),
        reverse=True,
    )
    selected_nodes = [nodes[item.node_id] for item in ordered if item.node_id in nodes]
    evidence_values: list[Any] = []
    for node in selected_nodes:
        metadata = _metadata(node)
        evidence_values.extend(
            [
                node.get("evidence_id"),
                metadata.get("evidence_id"),
                metadata.get("evidence_refs"),
                metadata.get("evidence_ref_id"),
                node.get("id") if str(node.get("kind") or "") == "EvidenceRef" else "",
            ]
        )
    code_nodes = [
        node
        for node in selected_nodes
        if str(node.get("kind") or "") in {"CodeNode", "CodeVersion", "CodeHunk", "Symbol", "SymbolVersion"}
    ]
    return {
        "packet_ids": _unique(_packet_id(node) for node in selected_nodes),
        "commit_shas": _unique(_commit_sha(node) for node in selected_nodes),
        "evidence_ids": _unique(evidence_values),
        "code_node_ids": _unique(node.get("id") for node in code_nodes),
        "code_nodes": _unique(node.get("label") or node.get("summary") for node in code_nodes)[:12],
        "neighbor_node_ids": _unique(node.get("id") for node in selected_nodes),
    }


def _trace_node(candidate: _TraceCandidate, node: dict[str, Any], *, role: str = "") -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "kind": node.get("kind"),
        "role": role or _reason_role(node),
        "label": node.get("label"),
        "summary": _clip(str(node.get("summary") or ""), 260),
        "packet_id": _packet_id(node),
        "commit_sha": _commit_sha(node),
        "evidence_id": node.get("evidence_id"),
        "distance": candidate.distance,
        "score": round(candidate.score, 6),
    }


def _edge_sort_key(edge: dict[str, Any], node: dict[str, Any], query_terms: set[str]) -> tuple[float, float]:
    return (
        TRACE_EDGE_PRIORITIES.get(str(edge.get("kind") or ""), 0.0),
        _node_score(node, query_terms),
    )


def _node_score(node: dict[str, Any], query_terms: set[str]) -> float:
    kind = str(node.get("kind") or "")
    overlap = _query_overlap(node, query_terms)
    return TRACE_KIND_PRIORITIES.get(kind, 0.2) + min(1.5, overlap * 0.25)


def _same_scope(node: dict[str, Any], *, seed_packet: str, seed_commit: str) -> bool:
    return bool(
        (seed_packet and _packet_id(node) == seed_packet)
        or (seed_commit and _commit_sha(node) == seed_commit)
    )


def _scope_score(node: dict[str, Any], *, seed_packet: str, seed_commit: str) -> int:
    score = 0
    if seed_packet and _packet_id(node) == seed_packet:
        score += 3
    if seed_commit and _commit_sha(node) == seed_commit:
        score += 2
    return score


def _query_overlap(node: dict[str, Any], query_terms: set[str]) -> int:
    text = _normalize(_node_text(node))
    return sum(1 for term in query_terms if _term_matches(term, text))


def _visible_query_overlap(node: dict[str, Any], query_terms: set[str]) -> int:
    text = _normalize(" ".join([str(node.get("label") or ""), str(node.get("summary") or "")]))
    return sum(1 for term in query_terms if _term_matches(term, text))


def _term_matches(term: str, text: str) -> bool:
    if term in text:
        return True
    if term.endswith("s") and len(term) > 3 and term[:-1] in text:
        return True
    return False


def _node_text(node: dict[str, Any]) -> str:
    return " ".join(
        [
            str(node.get("kind") or ""),
            str(node.get("label") or ""),
            str(node.get("summary") or ""),
            json.dumps(_metadata(node), sort_keys=True),
        ]
    )


def _reason_role(node: dict[str, Any]) -> str:
    metadata = _metadata(node)
    role = str(metadata.get("node_type") or metadata.get("type") or "").strip()
    if role:
        return role
    label = str(node.get("label") or "")
    if ":" in label:
        prefix = label.split(":", 1)[0].strip()
        if prefix in CHAIN_ROLES:
            return prefix
    return ""


def _metadata(node: dict[str, Any]) -> dict[str, Any]:
    metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    return {**metadata, **node_metadata}


def _packet_id(node: dict[str, Any]) -> str:
    metadata = _metadata(node)
    return str(
        node.get("packet_id")
        or metadata.get("packet_id")
        or metadata.get("source_packet_id")
        or ""
    )


def _commit_sha(node: dict[str, Any]) -> str:
    metadata = _metadata(node)
    return str(
        node.get("commit_sha")
        or node.get("commit_id")
        or metadata.get("commit_sha")
        or metadata.get("source_commit_sha")
        or ""
    )


def _short_node_text(node: dict[str, Any]) -> str:
    return _clip(str(node.get("summary") or node.get("label") or node.get("id") or ""), 160)


def _terms(text: str) -> set[str]:
    terms: set[str] = set()
    for token in re.split(r"[^a-zA-Z0-9_]+", str(text).lower()):
        if len(token) <= 2 or token in SEARCH_STOPWORDS:
            continue
        if re.fullmatch(r"[0-9a-f]{16,40}", token):
            continue
        terms.add(token)
    return terms


def _normalize(text: str) -> str:
    return " ".join(re.findall(r"[a-zA-Z0-9_./:-]+", str(text).lower()))


def _unique(values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
            return
        if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            for item in value:
                visit(item)
            return
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    visit(values)
    return out


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 13)].rstrip() + " ... <clipped>"
