from __future__ import annotations

import json
from typing import Any


PEER_ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
        "answer_grade": {"type": "boolean"},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "confidence", "answer_grade", "gaps"],
    "additionalProperties": False,
}


FINAL_SYNTHESIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "confidence": {"type": "number"},
        "mode": {"type": "string"},
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["answer", "confidence", "mode", "gaps"],
    "additionalProperties": False,
}


ROOM_CONTINUATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "peer_ids": {"type": "array", "items": {"type": "string"}},
        "query": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number"},
    },
    "required": ["action", "peer_ids", "query", "reason", "confidence"],
    "additionalProperties": False,
}


ROOM_SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary_md": {"type": "string"},
    },
    "required": ["summary_md"],
    "additionalProperties": False,
}


def peer_answer_prompt(
    *,
    query: str,
    retrieval_bundle: dict[str, Any],
    quality: dict[str, Any],
    room_context: dict[str, Any],
) -> str:
    return (
        "/no_think\n"
        "You are an AMO peer agent responding from your own local memory only. "
        "You cannot see the initiator's private memory. Use only the retrieval bundle and room context below. "
        "If the retrieval does not directly support an answer, set answer_grade=false and explain the gap. "
        "Return JSON only with answer, confidence, answer_grade, and gaps.\n\n"
        f"Query: {query}\n"
        f"Quality: {json.dumps(quality, ensure_ascii=False, sort_keys=True)}\n"
        f"Room context: {json.dumps(room_context, ensure_ascii=False, sort_keys=True)[:6000]}\n"
        f"Retrieval bundle: {json.dumps(retrieval_bundle, ensure_ascii=False, sort_keys=True)[:12000]}"
    )


def final_synthesis_prompt(
    *,
    query: str,
    local_result: dict[str, Any],
    peer_responses: list[dict[str, Any]],
) -> str:
    return (
        "/no_think\n"
        "You are the initiator-side AMO peer-agent synthesizer. Paid provider calls, if any, happen only here. "
        "Do not imply that peer-local packet or evidence ids are globally meaningful. Prefer portable shared refs "
        "like commits, paths, symbols, and claims. Keep peer claims grouped by source peer. Return JSON only with "
        "answer, confidence, mode, and gaps.\n\n"
        f"Query: {query}\n"
        f"Local result: {json.dumps(local_result, ensure_ascii=False, sort_keys=True)[:12000]}\n"
        f"Peer responses: {json.dumps(peer_responses, ensure_ascii=False, sort_keys=True)[:16000]}"
    )


def room_continuation_prompt(
    *,
    room_context: dict[str, Any],
    peer_responses: list[dict[str, Any]],
    agent_state: dict[str, Any],
) -> str:
    return (
        "/no_think\n"
        "You are the initiator-side AMO peer-agent conversation planner. "
        "Decide exactly one next action for this peer room. "
        "Use action=finalize only when the current peer responses answer the room goal. "
        "Use action=ask_peer when one peer should answer a focused follow-up. "
        "Use action=ask_peers when multiple peers should answer the same focused follow-up. "
        "Use action=wait when no useful peer response has arrived yet. "
        "Do not ask for raw evidence. Keep follow-up query short and specific. "
        "Return JSON only with action, peer_ids, query, reason, and confidence.\n\n"
        f"Room context: {json.dumps(room_context, ensure_ascii=False, sort_keys=True)[:14000]}\n"
        f"Peer responses: {json.dumps(peer_responses, ensure_ascii=False, sort_keys=True)[:12000]}\n"
        f"Agent state: {json.dumps(agent_state, ensure_ascii=False, sort_keys=True)[:5000]}"
    )


def room_summary_prompt(*, room_context: dict[str, Any]) -> str:
    return (
        "/no_think\n"
        "Summarize this AMO peer room for the initiator's rolling context window. "
        "Keep topic, participants, useful findings, open gaps, and next action. "
        "Do not include secrets or raw evidence. Return JSON only with summary_md.\n\n"
        f"Room context: {json.dumps(room_context, ensure_ascii=False, sort_keys=True)[:18000]}"
    )
