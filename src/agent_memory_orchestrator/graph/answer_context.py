from __future__ import annotations

import re
from typing import Any
from typing import Iterable

from .answer_trace import build_answer_trace
from .store import GraphStore
from .text_utils import _clip


def _answer_from_retrieval_result(
    result: dict[str, Any],
    *,
    graph_store: GraphStore | None = None,
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


def _version_timeline_context(
    *,
    query: str,
    items: list[dict[str, Any]],
    focused_items: list[dict[str, Any]],
) -> dict[str, Any]:
    target_paths = _timeline_target_paths(query=query, items=[*focused_items, *items])
    if not target_paths:
        return {}
    matching = [item for item in items if _timeline_item_matches_paths(item, target_paths)]
    if not matching:
        return {}
    commit_count = len({commit for item in matching for commit in _timeline_item_commits(item)})
    if not _version_timeline_requested(query) and commit_count < 2:
        return {}

    order: list[str] = []
    entries: dict[str, dict[str, Any]] = {}
    for item in matching:
        _merge_timeline_item(entries=entries, order=order, item=item, target_paths=target_paths)

    ordered_entries = [
        {field: value for field, value in entries[key].items() if not str(field).startswith("_")}
        for key in order
        if key in entries
    ]
    ordered_entries = [entry for entry in ordered_entries if entry.get("commit_sha") or entry.get("why") or entry.get("files")]
    ordered_entries = [entry for entry in ordered_entries if _timeline_entry_has_explanation(entry)]
    return {
        "target_paths": target_paths,
        "entries": ordered_entries[:8],
        "commit_count": len({str(entry.get("commit_sha") or "") for entry in ordered_entries if entry.get("commit_sha")}),
    }


def _version_timeline_requested(query: str) -> bool:
    lowered = str(query or "").lower()
    return bool(
        re.search(
            r"\b(version history|version flow|version chain|versions?|over time|evolved?|evolution|history|current)\b",
            lowered,
        )
    )


def _timeline_entry_has_explanation(entry: dict[str, Any]) -> bool:
    support = {str(value or "") for value in entry.get("support", []) if str(value or "").strip()}
    why = str(entry.get("why") or "").strip()
    message = str(entry.get("message") or "").strip()
    files = {_normalize_public_path(path) for path in entry.get("files", []) if str(path or "").strip()}
    path_only_why = bool(why) and why in files
    path_only_message = message.startswith("File version:") or message.startswith("Commit version:")
    if support == {"central version"} and (not why or path_only_why or path_only_message):
        return False
    return True


def _timeline_target_paths(*, query: str, items: list[dict[str, Any]]) -> list[str]:
    locator_terms = _answer_code_locator_terms(query)
    if not locator_terms:
        return []
    query_lower = str(query or "").lower()
    scores: dict[str, float] = {}
    for item in items:
        doc_type = str(item.get("doc_type") or "")
        base = 0.0
        if doc_type == "file_impact":
            base = 3.0
        elif doc_type in {"code_impact", "central_version"}:
            base = 2.0
        elif doc_type in {"reasoning", "packet"}:
            base = 1.0
        for path in _timeline_item_paths(item):
            normalized = _normalize_public_path(path)
            if not normalized:
                continue
            path_lower = normalized.lower()
            basename = path_lower.rsplit("/", 1)[-1]
            score = base
            for term in locator_terms:
                term_lower = term.lower()
                if term_lower == path_lower or term_lower == basename:
                    score += 8.0
                elif term_lower in path_lower:
                    score += 3.0
                elif term_lower.replace(".py", "") and term_lower.replace(".py", "") in path_lower:
                    score += 1.5
            if "test" not in query_lower and "/test" not in path_lower and not basename.startswith("test_"):
                score += 0.75
            if score > base:
                scores[normalized] = max(scores.get(normalized, 0.0), score)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [path for path, _score in ranked[:1]]


def _timeline_item_matches_paths(item: dict[str, Any], target_paths: list[str]) -> bool:
    normalized_targets = {_normalize_public_path(path).lower() for path in target_paths if path}
    item_paths = {_normalize_public_path(path).lower() for path in _timeline_item_paths(item)}
    if normalized_targets.intersection(item_paths):
        return True
    text = _timeline_item_text(item).lower()
    for target in normalized_targets:
        basename = target.rsplit("/", 1)[-1]
        if target and target in text:
            return True
        if basename and basename in text:
            return True
    return False


def _merge_timeline_item(
    *,
    entries: dict[str, dict[str, Any]],
    order: list[str],
    item: dict[str, Any],
    target_paths: list[str],
) -> None:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    doc_type = str(item.get("doc_type") or "")
    commits = _timeline_item_commits(item)
    if not commits:
        return
    messages = _unique_public_values(metadata.get("commit_messages"))
    packet_ids = _timeline_item_packets(item)
    evidence_ids = _timeline_item_evidence(item)
    files = [path for path in _timeline_item_paths(item) if path]
    reasons = _timeline_item_reasons(item)
    support_label = _timeline_support_label(doc_type)
    priority = _timeline_item_priority(item)

    for idx, commit in enumerate(commits):
        key = commit.lower()
        if key not in entries:
            entries[key] = {
                "commit_sha": commit,
                "message": "",
                "why": "",
                "files": [],
                "packets": [],
                "evidence": [],
                "support": [],
                "_message_priority": 0,
                "_why_priority": 0,
            }
            order.append(key)
        entry = entries[key]
        message = messages[idx] if idx < len(messages) else _timeline_message_from_item(item)
        if message and (not entry.get("message") or priority > int(entry.get("_message_priority") or 0)):
            entry["message"] = _public_answer_text(message)
            entry["_message_priority"] = priority
        reason = reasons[idx] if idx < len(reasons) else _timeline_reason_from_item(item, version_metadata)
        if reason and (not entry.get("why") or priority > int(entry.get("_why_priority") or 0)):
            entry["why"] = _public_answer_text(reason)
            entry["_why_priority"] = priority
        entry["files"] = _unique_public_values([entry.get("files"), files, target_paths])[:6]
        entry["packets"] = _unique_public_values([entry.get("packets"), packet_ids])[:6]
        entry["evidence"] = _unique_public_values([entry.get("evidence"), evidence_ids])[:8]
        support_parts = list(entry.get("support") or [])
        if support_label:
            support_parts.append(support_label)
        if packet_ids:
            support_parts.append("packet-backed")
        if evidence_ids:
            support_parts.append("evidence-backed")
        entry["support"] = _unique_public_values(support_parts)


def _timeline_item_commits(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    values: list[Any] = [
        item.get("commit_sha"),
        metadata.get("commit_sha"),
        metadata.get("source_commit_sha"),
        metadata.get("commit_shas"),
        version_metadata.get("producing_commit_sha"),
        version_metadata.get("linked_commits"),
    ]
    if str(node_metadata.get("atom_kind") or "") == "commit":
        values.append(_timeline_central_commit_sha(node_metadata, version_metadata))
    return [value for value in _unique_public_values(values) if value]


def _timeline_item_packets(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    support = item.get("support") if isinstance(item.get("support"), dict) else {}
    return _unique_public_values(
        [
            item.get("packet_id"),
            metadata.get("packet_id"),
            metadata.get("source_packet_id"),
            metadata.get("packet_ids"),
            version_metadata.get("linked_packets"),
            support.get("packet_ids"),
        ]
    )


def _timeline_item_evidence(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    support = item.get("support") if isinstance(item.get("support"), dict) else {}
    return _unique_public_values([metadata.get("evidence_refs"), version_metadata.get("evidence_refs"), support.get("evidence_ids")])


def _timeline_item_paths(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    values: list[Any] = [
        metadata.get("path"),
        metadata.get("file_path"),
        metadata.get("normalized_file_path"),
        metadata.get("selected_files"),
        metadata.get("changed_files"),
        version_metadata.get("linked_files"),
        version_metadata.get("file_path"),
    ]
    selected_file_roles = metadata.get("selected_file_roles")
    if isinstance(selected_file_roles, dict):
        values.append(list(selected_file_roles.keys()))
    file_path = _timeline_central_file_path(node_metadata, version_metadata) if node_metadata else ""
    if file_path:
        values.append(file_path)
    return [_normalize_public_path(value) for value in _unique_public_values(values) if value]


def _timeline_item_reasons(item: dict[str, Any]) -> list[str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
    version_metadata = node_metadata.get("version_metadata") if isinstance(node_metadata.get("version_metadata"), dict) else {}
    return _unique_public_values(
        [
            metadata.get("reasons"),
            metadata.get("reasoning_statements"),
            metadata.get("reason"),
            version_metadata.get("statement"),
            version_metadata.get("summary"),
            version_metadata.get("rationale"),
            item.get("reason"),
            item.get("statement"),
        ]
    )


def _timeline_reason_from_item(item: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    for value in (
        item.get("reason"),
        item.get("statement"),
        version_metadata.get("statement"),
        version_metadata.get("summary"),
        version_metadata.get("rationale"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _timeline_message_from_item(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    return re.sub(r"^WP\d+\s+", "", title).strip()


def _timeline_support_label(doc_type: str) -> str:
    labels = {
        "file_impact": "file-impact summary",
        "code_impact": "code-impact summary",
        "reasoning": "accepted reasoning",
        "central_version": "central version",
        "packet": "work packet",
        "symbol_ref": "symbol support",
        "code_region_ref": "code-region support",
    }
    return labels.get(str(doc_type or ""), "")


def _timeline_item_priority(item: dict[str, Any]) -> int:
    doc_type = str(item.get("doc_type") or "")
    if doc_type == "file_impact":
        return 50
    if doc_type == "code_impact":
        return 45
    if doc_type == "reasoning":
        return 42
    if doc_type == "central_version":
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        node_metadata = metadata.get("node_metadata") if isinstance(metadata.get("node_metadata"), dict) else {}
        return 43 if str(node_metadata.get("atom_kind") or "") in {"decision", "problem"} else 10
    if doc_type == "packet":
        return 35
    return 20


def _timeline_item_text(item: dict[str, Any]) -> str:
    return " ".join(
        str(item.get(key) or "")
        for key in ("title", "statement", "reason", "body")
    )


def _normalize_public_path(value: object) -> str:
    return str(value or "").strip().replace("\\", "/").lstrip("./")


def _timeline_central_file_path(metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    canonical_key = str(version_metadata.get("canonical_key") or metadata.get("canonical_key") or "")
    if canonical_key.startswith("file|"):
        parts = canonical_key.split("|", 2)
        return parts[-1] if len(parts) == 3 else ""
    return str(version_metadata.get("file_path") or "")


def _timeline_central_commit_sha(metadata: dict[str, Any], version_metadata: dict[str, Any]) -> str:
    canonical_key = str(version_metadata.get("canonical_key") or metadata.get("canonical_key") or "")
    if canonical_key.startswith("commit|"):
        parts = canonical_key.split("|", 2)
        return parts[-1] if len(parts) == 3 else ""
    return str(version_metadata.get("commit_sha") or "")


def _unique_public_values(values: Iterable[Any]) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        return _unique_nonempty([values])
    return _unique_nonempty(values)


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


def _answer_code_locator_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9_./:-]+", str(query or "")):
        lowered = token.lower().replace("\\", "/")
        if "_" in lowered or "::" in lowered or "/" in lowered or "." in lowered:
            terms.add(lowered)
            terms.update(part for part in re.split(r"[^a-zA-Z0-9_]+", lowered) if len(part) > 2)
    return terms


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


def _support_from_version_timeline(version_timeline: dict[str, Any]) -> dict[str, Any]:
    entries = version_timeline.get("entries") if isinstance(version_timeline.get("entries"), list) else []
    return {
        "packet_ids": _unique_public_values(entry.get("packets") for entry in entries if isinstance(entry, dict)),
        "commit_shas": _unique_public_values(entry.get("commit_sha") for entry in entries if isinstance(entry, dict)),
        "evidence_ids": _unique_public_values(entry.get("evidence") for entry in entries if isinstance(entry, dict)),
        "code_node_ids": [],
        "code_nodes": [],
        "neighbor_node_ids": [],
    }


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


def _public_answer_text(text: str) -> str:
    cleaned = re.sub(r"\{[^{}]{0,2000}\}", "", str(text or ""))
    cleaned = re.sub(r"\{.*$", "", cleaned)
    cleaned = re.sub(r"\b(?:FileImpactSummary|CodeImpactSummary|ReasoningNode):\s*", "", cleaned)
    cleaned = re.sub(r"\bImpact summary for\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bWP\d{3,}\b", "work item", cleaned)
    cleaned = re.sub(r"\bE\d{3,}\b", "evidence record", cleaned)
    cleaned = re.sub(r"\bpacket\s+work item\b", "work item", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwork packet\b", "work item", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bevidence\s+evidence record\b", "evidence record", cleaned, flags=re.IGNORECASE)
    return cleaned


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


def _unique_nonempty(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                visit(item)
            return
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)

    for value in values:
        visit(value)
    return out


def _body_field(body: str, field: str) -> str:
    prefix = f"{field.strip().lower()}:"
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(prefix):
            return stripped.split(":", 1)[-1].strip()
    return ""


def _best_answer_line(body: str) -> str:
    for prefix in ("statement:", "summary:", "reason:", "symbol:", "file_path:"):
        for line in body.splitlines():
            if line.strip().lower().startswith(prefix):
                return line.split(":", 1)[-1].strip()
    return body.strip().splitlines()[0][:300] if body.strip() else ""

