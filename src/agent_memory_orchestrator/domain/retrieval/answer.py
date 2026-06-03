from __future__ import annotations

from typing import Any

from .answer_timeline import _support_from_version_timeline
from .answer_timeline import _version_timeline_context
from .answer_trace import TraceGraphStore
from .answer_trace import build_answer_trace
from .answer_utils import _answer_code_locator_terms
from .answer_utils import _best_answer_line
from .answer_utils import _body_field
from .answer_utils import _public_answer_text
from .answer_utils import _unique_nonempty
from .answer_utils import _unique_public_values
from .text import clip_text as _clip


def _answer_from_retrieval_result(
    result: dict[str, Any],
    *,
    graph_store: TraceGraphStore | None = None,
    session_id: str = "",
) -> dict[str, Any]:
    hits = result.get("hits") if isinstance(result.get("hits"), list) else []
    if not hits:
        return {
            "text": "No indexed graph evidence matched the query.",
            "citations": [],
            "node_ids": [],
        }
    citations: list[dict[str, Any]] = []
    node_ids: list[str] = []
    context_items: list[dict[str, Any]] = []
    for index, hit in enumerate(hits[:20], start=1):
        doc = hit.get("document") if isinstance(hit, dict) and isinstance(hit.get("document"), dict) else {}
        graph_node = hit.get("graph_node") if isinstance(hit, dict) and isinstance(hit.get("graph_node"), dict) else {}
        neighbors = hit.get("neighbors") if isinstance(hit, dict) and isinstance(hit.get("neighbors"), list) else []
        node_id = str(doc.get("graph_node_id") or graph_node.get("id") or "")
        node_ids.append(node_id)
        title = _public_answer_title(doc=doc, graph_node=graph_node, fallback=node_id)
        body = str(doc.get("body") or graph_node.get("summary") or "")
        statement = _public_answer_statement(doc=doc, graph_node=graph_node, body=body)
        reason = _body_field(body, "reason")
        trace = (
            build_answer_trace(
                seed_node_id=node_id,
                graph_store=graph_store,
                query=str(result.get("query") or ""),
                session_id=session_id,
            )
            if graph_store is not None and node_id
            else {}
        )
        support = _answer_support(doc=doc, graph_node=graph_node, neighbors=neighbors, trace=trace)
        if not trace.get("node_count"):
            trace = _fallback_trace_from_retrieval_doc(doc=doc, node_id=node_id, support=support)
        context_items.append(
            {
                "rank": index,
                "doc_type": str(doc.get("doc_type") or ""),
                "node_kind": str(doc.get("node_kind") or graph_node.get("kind") or ""),
                "packet_id": str(doc.get("packet_id") or ""),
                "commit_sha": str(doc.get("commit_sha") or ""),
                "title": title,
                "statement": statement,
                "reason": _public_answer_text(reason),
                "body": body,
                "metadata": doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {},
                "support": support,
                "trace": trace,
                "score": hit.get("score"),
            }
        )
        if index <= 8:
            citations.append(
                {
                    "rank": index,
                    "doc_id": doc.get("doc_id"),
                    "graph_node_id": node_id,
                    "doc_type": doc.get("doc_type"),
                    "packet_id": doc.get("packet_id"),
                    "commit_sha": doc.get("commit_sha"),
                    "packet_ids": support["packet_ids"],
                    "commit_shas": support["commit_shas"],
                    "evidence_ids": support["evidence_ids"],
                    "code_node_ids": support["code_node_ids"],
                    "code_nodes": support["code_nodes"],
                    "neighbor_node_ids": support["neighbor_node_ids"],
                    "trace": trace,
                    "score": hit.get("score"),
                }
            )
    return {
        "text": _render_retrieval_context_answer(query=str(result.get("query") or ""), items=context_items),
        "citations": citations,
        "node_ids": [node_id for node_id in node_ids if node_id],
        "context": _retrieval_context_payload(query=str(result.get("query") or ""), items=context_items),
    }


def _render_retrieval_context_answer(*, query: str, items: list[dict[str, Any]]) -> str:
    focused_items = _focused_context_items(query=query, items=items)
    if not focused_items:
        return "No indexed graph evidence matched the query."
    lines = ["Answer from repository memory:", "Use this as retrieval context for synthesis, not final prose.", f"Query: {query}"]
    version_timeline = _version_timeline_context(query=query, items=items, focused_items=focused_items)
    if version_timeline.get("entries"):
        target = ", ".join(version_timeline.get("target_paths") or [])
        heading = f"Version history for {target}:" if target else "Version history:"
        lines.extend(["", heading])
        for idx, entry in enumerate(version_timeline["entries"][:6], start=1):
            commit = str(entry.get("commit_sha") or "").strip()
            message = str(entry.get("message") or "").strip()
            label = " ".join(part for part in (commit[:7], message) if part).strip() or "versioned change"
            lines.append(f"{idx}. {label}")
            why = str(entry.get("why") or "").strip()
            if why:
                lines.append(f"   Why: {_clip(why, 260)}")
            files = entry.get("files") if isinstance(entry.get("files"), list) else []
            if files:
                lines.append("   Files: " + ", ".join(str(path) for path in files[:4]))
            support = entry.get("support") if isinstance(entry.get("support"), list) else []
            if support:
                lines.append("   Support: " + ", ".join(str(value) for value in support[:5]))
    discussion = _context_bucket(focused_items, {"packet"})
    reasoning = _context_bucket(focused_items, {"central_version", "reasoning"})
    implementation = _context_bucket(focused_items, {"code_impact", "file_impact", "file_ref", "symbol_ref", "code_region_ref"})

    if discussion:
        lines.extend(["", "Relevant work and discussion:"])
        for idx, item in enumerate(discussion[:3], start=1):
            lines.append(f"{idx}. {_context_line(item)}")
    if reasoning:
        lines.extend(["", "Decisions and reasoning:"])
        for idx, item in enumerate(reasoning[:4], start=1):
            lines.append(f"{idx}. {_context_line(item)}")
    if implementation:
        lines.extend(["", "Code and file support:"])
        for idx, item in enumerate(implementation[:5], start=1):
            lines.append(f"{idx}. {_context_line(item)}")

    support = _merged_support(focused_items)
    if version_timeline.get("entries"):
        support = _merge_public_support(support, _support_from_version_timeline(version_timeline))
    support_summary = _public_support_summary(support)
    trace_parts: list[str] = []
    if support.get("packet_ids"):
        trace_parts.append(f"packets={len(support['packet_ids'])}")
    if support.get("commit_shas"):
        trace_parts.append("commits=" + ", ".join(support["commit_shas"][:5]))
    if support.get("evidence_ids"):
        trace_parts.append(f"evidence={len(support['evidence_ids'])}")
    if support.get("code_nodes") or support.get("code_node_ids"):
        trace_parts.append(f"code_refs={len(support.get('code_nodes') or support.get('code_node_ids') or [])}")
    if support_summary or trace_parts:
        lines.extend(["", "Trace support:"])
        if support_summary:
            lines.append(f"Support: {support_summary}")
        if trace_parts:
            lines.append("Evidence: " + "; ".join(trace_parts))
    return "\n".join(lines)


def _retrieval_context_payload(*, query: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    focused_items = _focused_context_items(query=query, items=items)
    return {
        "query": query,
        "version_timeline": _version_timeline_context(query=query, items=items, focused_items=focused_items),
        "items": [
            {
                "rank": item.get("rank"),
                "doc_type": item.get("doc_type"),
                "node_kind": item.get("node_kind"),
                "title": item.get("title"),
                "statement": item.get("statement"),
                "reason": item.get("reason"),
                "score": item.get("score"),
                "support": item.get("support"),
            }
            for item in focused_items
        ],
        "support": _merged_support(focused_items),
    }





def _focused_context_items(*, query: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    locator_terms = _answer_code_locator_terms(query)
    if not locator_terms:
        return items
    focused = [item for item in items if _item_matches_terms(item, locator_terms)]
    if not focused:
        return items
    support = _merged_support(focused)
    packet_ids = set(support.get("packet_ids") or [])
    commit_shas = set(support.get("commit_shas") or [])
    out: list[dict[str, Any]] = []
    for item in items:
        item_support = item.get("support") if isinstance(item.get("support"), dict) else {}
        shares_anchor = bool(packet_ids.intersection(item_support.get("packet_ids") or [])) or bool(
            commit_shas.intersection(item_support.get("commit_shas") or [])
        )
        if _item_matches_terms(item, locator_terms) or shares_anchor:
            out.append(item)
    return out or focused



def _item_matches_terms(item: dict[str, Any], terms: set[str]) -> bool:
    text = " ".join(str(item.get(key) or "") for key in ("title", "statement", "reason")).lower()
    return any(term in text for term in terms)


def _context_bucket(items: list[dict[str, Any]], doc_types: set[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        doc_type = str(item.get("doc_type") or "")
        if doc_type not in doc_types:
            continue
        if doc_type == "central_version" and not _central_version_context_is_reasoning(item):
            continue
        title = str(item.get("title") or "")
        statement = str(item.get("statement") or "")
        key = (title.lower(), statement.lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _central_version_context_is_reasoning(item: dict[str, Any]) -> bool:
    title = str(item.get("title") or "").strip().lower()
    if title.startswith(("decision:", "problem:")):
        return True
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    return str(node_metadata.get("atom_kind") or "") in {"decision", "problem"}


def _context_line(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    statement = str(item.get("statement") or "").strip()
    reason = str(item.get("reason") or "").strip()
    parts = []
    if title:
        parts.append(title)
    if statement and statement.lower() != title.lower():
        parts.append(statement)
    if reason and reason.lower() not in {statement.lower(), title.lower()}:
        parts.append(f"reason: {reason}")
    line = " - ".join(parts) if parts else "retrieved support"
    return _clip(line, 520)



def _merge_public_support(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    keys = ("packet_ids", "commit_shas", "evidence_ids", "code_node_ids", "code_nodes", "neighbor_node_ids")
    return {key: _unique_public_values([left.get(key), right.get(key)]) for key in keys}


def _merged_support(items: list[dict[str, Any]]) -> dict[str, Any]:
    packet_ids: list[Any] = []
    commit_shas: list[Any] = []
    evidence_ids: list[Any] = []
    code_node_ids: list[Any] = []
    code_nodes: list[Any] = []
    neighbor_node_ids: list[Any] = []
    for item in items:
        support = item.get("support") if isinstance(item.get("support"), dict) else {}
        packet_ids.append(support.get("packet_ids"))
        commit_shas.append(support.get("commit_shas"))
        evidence_ids.append(support.get("evidence_ids"))
        code_node_ids.append(support.get("code_node_ids"))
        code_nodes.append(support.get("code_nodes"))
        neighbor_node_ids.append(support.get("neighbor_node_ids"))
    return {
        "packet_ids": _unique_nonempty(packet_ids),
        "commit_shas": _unique_nonempty(commit_shas),
        "evidence_ids": _unique_nonempty(evidence_ids),
        "code_node_ids": _unique_nonempty(code_node_ids),
        "code_nodes": _unique_nonempty(code_nodes)[:12],
        "neighbor_node_ids": _unique_nonempty(neighbor_node_ids),
    }


def _fallback_trace_from_retrieval_doc(*, doc: dict[str, Any], node_id: str, support: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal trace when a curated retrieval doc is not in this graph.

    Repo central retrieval often opens the central Kuzu graph while support docs
    still point at curated session graph ids. In that case graph traversal cannot
    start from the support doc, but the retrieval projection already carries the
    packet/commit/evidence/file provenance that must not be hidden.
    """

    if not any(support.get(key) for key in ("packet_ids", "commit_shas", "evidence_ids", "code_node_ids", "code_nodes")):
        return {}
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    seed = {
        "id": node_id or doc.get("doc_id"),
        "kind": doc.get("node_kind") or doc.get("doc_type") or "RetrievalDocument",
        "role": doc.get("doc_type") or doc.get("node_kind") or "support",
        "label": doc.get("title") or node_id or doc.get("doc_id"),
        "summary": _clip(str(doc.get("body") or ""), 260),
        "packet_id": doc.get("packet_id") or metadata.get("packet_id"),
        "commit_sha": doc.get("commit_sha") or metadata.get("commit_sha"),
        "evidence_id": "",
        "distance": 0,
        "score": 0.0,
    }
    return {
        "seed_node_id": node_id,
        "max_depth": 0,
        "node_count": 1,
        "source": "retrieval_document_metadata",
        "chain": [seed],
        "packets": [{"id": value, "kind": "Packet", "label": value} for value in support.get("packet_ids", [])[:3]],
        "commits": [{"id": value, "kind": "Commit", "label": value} for value in support.get("commit_shas", [])[:4]],
        "evidence": [{"id": value, "kind": "EvidenceRef", "label": value} for value in support.get("evidence_ids", [])[:5]],
        "code_hunks": [],
        "code_nodes": [{"id": value, "kind": "CodeRef", "label": value} for value in support.get("code_node_ids", [])[:8]],
        "symbols": [],
        "paths": [],
        "support": {
            "packet_ids": support.get("packet_ids", []),
            "commit_shas": support.get("commit_shas", []),
            "evidence_ids": support.get("evidence_ids", []),
            "code_node_ids": support.get("code_node_ids", []),
            "code_nodes": support.get("code_nodes", []),
            "neighbor_node_ids": support.get("neighbor_node_ids", []),
        },
    }


def _public_trace_summary(trace: dict[str, Any]) -> str:
    if not trace or not trace.get("node_count"):
        return ""
    chain_parts: list[str] = []
    for item in trace.get("chain") or []:
        role = str(item.get("role") or "").strip()
        kind = str(item.get("kind") or "").strip()
        public_role = _public_trace_role(role or kind)
        if public_role == "accepted reasoning":
            summary = _public_answer_text(str(item.get("summary") or item.get("label") or ""))
            value = f"{public_role}: {summary}" if summary else public_role
        else:
            value = public_role
        if value and value not in chain_parts:
            chain_parts.append(value)
    support_parts: list[str] = []
    support = trace.get("support") if isinstance(trace.get("support"), dict) else {}
    if support.get("commit_shas"):
        support_parts.append("commit-backed")
    if support.get("evidence_ids"):
        support_parts.append("evidence-backed")
    if support.get("code_nodes"):
        support_parts.append("code-backed")
    return " -> ".join([*chain_parts[:4], *support_parts])


def _public_trace_role(role: str) -> str:
    normalized = str(role or "").strip().lower()
    labels = {
        "central_version": "active memory version",
        "knowledgeversion": "active memory version",
        "reasoning": "accepted reasoning",
        "reasoningnode": "accepted reasoning",
        "file_impact": "changed file",
        "fileimpactsummary": "changed file",
        "code_impact": "implementation change",
        "codeimpactsummary": "implementation change",
        "commit": "commit support",
        "packet": "session support",
        "evidence": "evidence support",
        "evidenceref": "evidence support",
    }
    return labels.get(normalized, "")


def _public_support_summary(support: dict[str, Any]) -> str:
    parts: list[str] = []
    if support.get("packet_ids"):
        parts.append("session context")
    if support.get("commit_shas"):
        parts.append("commit-backed")
    if support.get("evidence_ids"):
        parts.append("evidence-backed")
    if support.get("code_nodes") or support.get("code_node_ids"):
        parts.append("code-linked")
    return ", ".join(parts)


def _public_answer_title(*, doc: dict[str, Any], graph_node: dict[str, Any], fallback: str) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_type = str(doc.get("doc_type") or "").strip().lower()
    if doc_type == "file_impact":
        path = str(metadata.get("path") or "").strip()
        if path:
            return f"Changes in {path}"
    if doc_type == "code_impact":
        commit_messages = metadata.get("commit_messages") if isinstance(metadata.get("commit_messages"), list) else []
        if commit_messages:
            return _public_answer_text(str(commit_messages[0]))
    return _public_answer_text(str(doc.get("title") or graph_node.get("label") or fallback))


def _public_answer_statement(*, doc: dict[str, Any], graph_node: dict[str, Any], body: str) -> str:
    metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    doc_type = str(doc.get("doc_type") or "").strip().lower()
    if doc_type == "packet":
        for line in body.splitlines():
            cleaned = _public_answer_text(line).strip()
            if cleaned and not cleaned.lower().startswith("packet:"):
                return cleaned
    if doc_type == "file_impact":
        path = str(metadata.get("path") or "").strip()
        reasons = metadata.get("reasons") if isinstance(metadata.get("reasons"), list) else []
        reason = _public_answer_text(str(reasons[0])) if reasons else ""
        if path and reason:
            return f"{path} changed because {reason}"
        if path:
            return f"{path} changed in the retrieved work."
    if doc_type == "code_impact":
        reason = _public_answer_text(str(metadata.get("reason") or ""))
        if reason:
            return reason
    if doc_type == "reasoning":
        statement = _public_answer_text(str(metadata.get("statement") or ""))
        if statement:
            return statement
    return _public_answer_text(_body_field(body, "statement") or _best_answer_line(body) or str(graph_node.get("summary") or ""))



def _answer_support(
    *,
    doc: dict[str, Any],
    graph_node: dict[str, Any],
    neighbors: list[dict[str, Any]],
    trace: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = graph_node.get("metadata") if isinstance(graph_node.get("metadata"), dict) else {}
    doc_meta = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
    trace_support = trace.get("support") if isinstance(trace, dict) and isinstance(trace.get("support"), dict) else {}
    packet_ids = _unique_nonempty(
        [
            doc.get("packet_id"),
            graph_node.get("packet_id"),
            metadata.get("packet_id"),
            metadata.get("source_packet_id"),
            *(neighbor.get("packet_id") for neighbor in neighbors),
            *(neighbor.get("label") for neighbor in neighbors if str(neighbor.get("kind") or "") == "Packet"),
            trace_support.get("packet_ids"),
        ]
    )
    commit_shas = _unique_nonempty(
        [
            doc.get("commit_sha"),
            graph_node.get("commit_sha"),
            graph_node.get("commit_id"),
            metadata.get("commit_sha"),
            metadata.get("source_commit_sha"),
            *(neighbor.get("commit_sha") for neighbor in neighbors),
            *(neighbor.get("commit_id") for neighbor in neighbors),
            trace_support.get("commit_shas"),
        ]
    )
    evidence_values: list[Any] = [
        graph_node.get("evidence_id"),
        metadata.get("evidence_id"),
        metadata.get("evidence_refs"),
        doc_meta.get("evidence_refs"),
    ]
    evidence_values.extend(neighbor.get("evidence_id") for neighbor in neighbors)
    evidence_values.extend(neighbor.get("id") for neighbor in neighbors if str(neighbor.get("kind") or "") == "EvidenceRef")
    evidence_values.extend(trace_support.get("evidence_ids") or [])
    evidence_ids = _unique_nonempty(evidence_values)
    code_neighbors = [
        neighbor
        for neighbor in neighbors
        if str(neighbor.get("kind") or "") in {"CodeNode", "CodeVersion", "CodeHunk", "Symbol", "SymbolVersion"}
    ]
    code_node_ids = _unique_nonempty(
        [
            *(neighbor.get("id") for neighbor in code_neighbors),
            trace_support.get("code_node_ids"),
        ]
    )
    code_nodes = _unique_nonempty(
        [
            *(neighbor.get("label") or neighbor.get("summary") for neighbor in code_neighbors),
            trace_support.get("code_nodes"),
        ]
    )[:8]
    neighbor_node_ids = _unique_nonempty(
        [
            *(neighbor.get("id") for neighbor in neighbors),
            trace_support.get("neighbor_node_ids"),
        ]
    )
    summary_parts = []
    if packet_ids:
        summary_parts.append("packet " + ", ".join(packet_ids[:3]))
    if commit_shas:
        summary_parts.append("commit " + ", ".join(commit_shas[:3]))
    if evidence_ids:
        summary_parts.append("evidence " + ", ".join(evidence_ids[:3]))
    if code_nodes:
        summary_parts.append("code " + "; ".join(code_nodes[:3]))
    return {
        "packet_ids": packet_ids,
        "commit_shas": commit_shas,
        "evidence_ids": evidence_ids,
        "code_node_ids": code_node_ids,
        "code_nodes": code_nodes,
        "neighbor_node_ids": neighbor_node_ids,
        "summary": " | ".join(summary_parts),
    }





