from __future__ import annotations

import re
from typing import Any

from .answer_utils import _answer_code_locator_terms
from .answer_utils import _normalize_public_path
from .answer_utils import _public_answer_text
from .answer_utils import _unique_public_values


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



__all__ = ["_support_from_version_timeline", "_version_timeline_context"]
