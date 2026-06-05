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
    room_context_char_limit: int = 1050,
    retrieval_char_limit: int = 950,
    answer_max_words: int = 90,
) -> str:
    return (
        "/no_think\n"
        "You are an AMO peer agent. Answer only from your local retrieval and the compact room context. "
        "Do not expose raw evidence. If retrieval is not direct, set answer_grade=false. "
        f"Keep answer under {max(1, int(answer_max_words))} words and gaps under 2 short items. "
        "Return JSON only: answer, confidence, answer_grade, gaps.\n\n"
        f"Query: {query}\n"
        f"Quality: {_compact_quality_text(quality)}\n\n"
        f"Room context:\n{_peer_answer_room_context_text(room_context, limit=max(300, int(room_context_char_limit)))}\n\n"
        f"Local retrieval:\n{_retrieval_text(retrieval_bundle, limit=max(300, int(retrieval_char_limit)))}"
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
        f"Local result:\n{_retrieval_text(local_result, limit=5000)}\n\n"
        f"Peer responses:\n{_peer_responses_text(peer_responses, limit=7000)}"
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
        f"Room context:\n{_room_context_text(room_context, limit=8000)}\n\n"
        f"Peer responses:\n{_peer_responses_text(peer_responses, limit=6000)}\n\n"
        f"Agent state:\n{_agent_state_text(agent_state, limit=2500)}"
    )


def room_summary_prompt(*, room_context: dict[str, Any]) -> str:
    return (
        "/no_think\n"
        "Summarize this AMO peer room for the initiator's rolling context window. "
        "Keep topic, participants, useful findings, open gaps, and next action. "
        "Summarize only durable room progress; do not repeat the full transcript. "
        "Do not include secrets or raw evidence. Return JSON only with summary_md.\n\n"
        f"Room context:\n{_room_context_text(room_context, limit=9000)}"
    )


def _room_context_text(room_context: dict[str, Any], *, limit: int) -> str:
    text = str(room_context.get("context_text") or "").strip()
    if not text:
        text = json.dumps(room_context.get("layers", room_context), ensure_ascii=False, sort_keys=True)
    return _clip(text, limit)


def _peer_answer_room_context_text(room_context: dict[str, Any], *, limit: int) -> str:
    layers = room_context.get("layers") if isinstance(room_context.get("layers"), dict) else {}
    if not layers:
        return _room_context_text(room_context, limit=limit)
    lines = [
        "Layer 1 - Room Brief",
        _compact_room_brief(str(layers.get("room_md") or "")),
        "",
        "Layer 2 - Rolling Summary",
        _clip(str(layers.get("rolling_summary_md") or "").strip() or "- No summary yet.", 250),
        "",
        "Layer 3A - Active Room Discussion",
        _messages_text(layers.get("active_recent_messages"), limit=230, empty="- No active room discussion yet."),
        "",
        "Layer 3B - Tagged Initiator/Peer Exchange",
        _messages_text(layers.get("pairwise_recent_messages"), limit=260, empty="- No tagged peer exchange yet."),
    ]
    return _clip("\n".join(lines), limit)


def _compact_room_brief(room_md: str) -> str:
    lines = []
    capture = False
    for raw_line in str(room_md or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("room_id:") or line.startswith("initiator:"):
            lines.append(line)
            continue
        if line in {"## Topic", "## Participants", "## Share Boundary"}:
            capture = True
            lines.append(line.replace("## ", ""))
            continue
        if line.startswith("## "):
            capture = False
            continue
        if capture:
            lines.append(line)
    return _clip("\n".join(lines) if lines else room_md, 300)


def _messages_text(value: Any, *, limit: int, empty: str) -> str:
    if not isinstance(value, list) or not value:
        return empty
    lines = []
    for message in value[-2:]:
        if not isinstance(message, dict):
            continue
        sender = str(message.get("from_node_id") or message.get("from") or "unknown").strip()
        recipients = message.get("to_node_ids") if isinstance(message.get("to_node_ids"), list) else []
        to_text = f" -> {','.join(str(item) for item in recipients if item)}" if recipients else ""
        message_type = str(message.get("type") or "peer_message").strip()
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        mode = str(metadata.get("mode") or "").strip()
        mode_text = f" mode={mode}" if mode else ""
        content = _clip(str(message.get("content") or "").strip(), 110)
        lines.append(f"- [{message_type}{mode_text}] {sender}{to_text}: {content}")
    return _clip("\n".join(lines) if lines else empty, limit)


def _retrieval_text(retrieval_bundle: dict[str, Any], *, limit: int) -> str:
    answer = retrieval_bundle.get("answer") if isinstance(retrieval_bundle.get("answer"), dict) else {}
    support = retrieval_bundle.get("support") if isinstance(retrieval_bundle.get("support"), list) else []
    lines = [f"Answer: {str(answer.get('text') or '').strip()}"]
    if support:
        lines.append("Support:")
    for item in support[:5]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or "").strip()
        shared_ref = item.get("shared_ref") if isinstance(item.get("shared_ref"), dict) else {}
        ref_bits = []
        for key in ("repo", "commit", "path", "symbol", "code_node_id"):
            if shared_ref.get(key):
                ref_bits.append(f"{key}={shared_ref[key]}")
        ref_text = f" ({'; '.join(ref_bits)})" if ref_bits else ""
        if claim:
            lines.append(f"- {claim}{ref_text}")
    return _clip("\n".join(lines), limit)


def _peer_responses_text(peer_responses: list[dict[str, Any]], *, limit: int) -> str:
    lines = []
    for response in peer_responses[:8]:
        if not isinstance(response, dict):
            continue
        source = response.get("source_peer") or response.get("from_node_id") or response.get("from") or "peer"
        mode = response.get("mode") or (response.get("metadata") or {}).get("mode", "")
        confidence = response.get("confidence")
        content = str(response.get("content") or "").strip()
        lines.append(f"- {source} mode={mode} confidence={confidence}: {content}")
    return _clip("\n".join(lines) if lines else "- No peer responses yet.", limit)


def _agent_state_text(agent_state: dict[str, Any], *, limit: int) -> str:
    compact = {
        "status": agent_state.get("status"),
        "original_query": agent_state.get("original_query"),
        "deadline_at": agent_state.get("deadline_at"),
        "peer_request_count": len(agent_state.get("peer_requests", [])) if isinstance(agent_state.get("peer_requests"), list) else 0,
    }
    return _clip(json.dumps(compact, ensure_ascii=False, sort_keys=True), limit)


def _compact_quality_text(quality: dict[str, Any]) -> str:
    compact = {
        "answer_grade": quality.get("answer_grade"),
        "confidence": quality.get("confidence"),
        "intent_match": quality.get("intent_match"),
        "citation_count": quality.get("citation_count"),
        "gaps": quality.get("gaps", [])[:4] if isinstance(quality.get("gaps"), list) else [],
    }
    return json.dumps(compact, ensure_ascii=False, sort_keys=True)


def _clip(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 15)].rstrip() + "...<clipped>"
