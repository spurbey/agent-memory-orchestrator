from __future__ import annotations

from dataclasses import dataclass
from typing import Any


DECISION_PACKET_SCHEMA_VERSION = "decision-packet-v1"
DEFAULT_CHUNK_TEXT_LIMIT = 3000


@dataclass(slots=True, frozen=True)
class DecisionPacket:
    session_id: str
    extraction_run_id: str
    commit_id: str
    payload: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return self.payload


def build_decision_packet(
    *,
    commit_window: dict[str, Any],
    work_change: dict[str, Any],
    chunks: list[dict[str, Any]],
    extraction_run_id: str,
    chunk_text_limit: int = DEFAULT_CHUNK_TEXT_LIMIT,
) -> DecisionPacket:
    """Build the structured input packet for Qwen decision enrichment.

    The packet is commit-scoped. Git/work-change facts provide the factual
    spine; chunks provide cleaned context and allowed evidence ids. The LLM is
    expected to extract only durable reasoning claims from this packet.
    """

    commit_id = str(commit_window.get("commit_id") or work_change.get("metadata", {}).get("commit_id") or "").strip()
    session_id = str(commit_window.get("session_id") or work_change.get("session_id") or "").strip()
    window_id = str(commit_window.get("window_id") or work_change.get("metadata", {}).get("window_id") or "").strip()
    scoped_chunks = [chunk for chunk in chunks if str(chunk.get("commit_id") or "") == commit_id]
    allowed_event_ids = _allowed_event_ids(scoped_chunks, commit_window)
    payload = {
        "schema_version": DECISION_PACKET_SCHEMA_VERSION,
        "session_id": session_id,
        "extraction_run_id": extraction_run_id,
        "commit_id": commit_id,
        "window_id": window_id,
        "work_change": {
            "id": str(work_change.get("id") or ""),
            "summary": str(work_change.get("summary") or ""),
            "kind": str(work_change.get("kind") or "WorkChange"),
            "evidence_ids": _strings(work_change.get("evidence_ids")),
            "metadata": {
                "commit_message": str(work_change.get("metadata", {}).get("commit_message") or commit_window.get("message") or ""),
                "commit_category": str(work_change.get("metadata", {}).get("commit_category") or ""),
                "git_changed_files": _strings(work_change.get("metadata", {}).get("git_changed_files") or commit_window.get("git_changed_files")),
            },
        },
        "commit_truth": {
            "full_sha": str(commit_window.get("full_sha") or work_change.get("metadata", {}).get("full_sha") or ""),
            "parent_shas": _strings(commit_window.get("parent_shas")),
            "message": str(commit_window.get("message") or ""),
            "git_changed_files": _strings(commit_window.get("git_changed_files")),
            "git_name_status": _git_name_status(commit_window.get("git_name_status")),
            "tool_kind_counts": commit_window.get("tool_kind_counts") if isinstance(commit_window.get("tool_kind_counts"), dict) else {},
            "diagnostics": _strings(commit_window.get("diagnostics")),
        },
        "chunks": [_packet_chunk(chunk, chunk_text_limit=chunk_text_limit) for chunk in scoped_chunks],
        "allowed_evidence_event_ids": allowed_event_ids,
        "output_contract": {
            "decisions": [
                {
                    "decision_type": "planned_action|completed_fix|investigation_result|constraint|revert|open_question",
                    "subject": "string",
                    "predicate": "string",
                    "object": "string",
                    "reason": "string",
                    "confidence": 0.0,
                    "evidence_event_ids": ["must be copied from allowed_evidence_event_ids"],
                }
            ]
        },
        "extraction_rules": [
            "Return durable why/decision/fix/bug/constraint/open_question claims only.",
            "Do not output a decision for a write_patch by itself; write_patch is code evidence.",
            "Use user/assistant event ids for intent and tool/test event ids only as supporting evidence.",
            "If no durable reasoning claim exists, return an empty decisions array.",
            "Do not invent event ids, files, commits, tests, or reasons.",
        ],
    }
    return DecisionPacket(session_id=session_id, extraction_run_id=extraction_run_id, commit_id=commit_id, payload=payload)


def build_decision_packets(
    *,
    commit_windows: list[dict[str, Any]],
    work_changes: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
    extraction_run_id: str,
    chunk_text_limit: int = DEFAULT_CHUNK_TEXT_LIMIT,
) -> tuple[DecisionPacket, ...]:
    work_by_commit = {
        str(item.get("metadata", {}).get("commit_id") or "").strip(): item
        for item in work_changes
        if str(item.get("metadata", {}).get("commit_id") or "").strip()
    }
    packets: list[DecisionPacket] = []
    for window in commit_windows:
        commit_id = str(window.get("commit_id") or "").strip()
        if not commit_id or not window.get("full_sha") or commit_id not in work_by_commit:
            continue
        packets.append(
            build_decision_packet(
                commit_window=window,
                work_change=work_by_commit[commit_id],
                chunks=chunks,
                extraction_run_id=extraction_run_id,
                chunk_text_limit=chunk_text_limit,
            )
        )
    return tuple(packets)


def _packet_chunk(chunk: dict[str, Any], *, chunk_text_limit: int) -> dict[str, Any]:
    text = str(chunk.get("embedding_text") or "")
    return {
        "chunk_id": str(chunk.get("chunk_id") or ""),
        "chunk_type": str(chunk.get("chunk_type") or ""),
        "group_key": str(chunk.get("group_key") or ""),
        "git_changed_files": _strings(chunk.get("git_changed_files")),
        "message_event_ids": _strings(chunk.get("message_event_ids")),
        "read_fact_event_ids": _strings(chunk.get("read_fact_event_ids")),
        "write_fact_event_ids": _strings(chunk.get("write_fact_event_ids")),
        "validation_event_ids": _strings(chunk.get("validation_event_ids")),
        "support_event_ids": _strings(chunk.get("support_event_ids"))[:50],
        "embedding_text_excerpt": text[:chunk_text_limit],
        "text_truncated": len(text) > chunk_text_limit,
    }


def _allowed_event_ids(chunks: list[dict[str, Any]], commit_window: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for chunk in chunks:
        for key in (
            "message_event_ids",
            "read_fact_event_ids",
            "write_fact_event_ids",
            "validation_event_ids",
            "embedding_event_ids",
        ):
            values.extend(_strings(chunk.get(key)))
    if not values:
        values.extend(_strings(commit_window.get("source_event_ids"))[:100])
    return _dedupe(values)


def _git_name_status(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        out.append({"status": str(item.get("status") or ""), "path": str(item.get("path") or "")})
    return out


def _strings(value: Any) -> list[str]:
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
