from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any


def _retrieval_intent(result: dict[str, Any]) -> str:
    retrieval = result.get("retrieval") if isinstance(result.get("retrieval"), dict) else {}
    return str(retrieval.get("intent") or "")


def _answer_text(result: dict[str, Any]) -> str:
    answer = result.get("answer") if isinstance(result.get("answer"), dict) else {}
    return str(answer.get("text") or "").strip()


def _supports_from_responses(responses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    supports: list[dict[str, Any]] = []
    for response in responses:
        response_support = response.get("support") if isinstance(response.get("support"), list) else []
        for support in response_support:
            if isinstance(support, dict):
                supports.append(support)
    return supports


def _retrieval_only_answer(local_result: Any, responses: list[dict[str, Any]]) -> str:
    if isinstance(local_result, dict):
        local_answer = str((local_result.get("answer") or {}).get("text") or "")
        if local_answer:
            return local_answer
    lines = ["No final LLM synthesis was available. Structured retrieval results:"]
    for index, response in enumerate(responses[:5], start=1):
        lines.append(f"{index}. {response.get('source_peer')}: {response.get('content')}")
    return "\n".join(lines)


def _deterministic_summary(context: dict[str, Any]) -> str:
    room = context.get("room") if isinstance(context.get("room"), dict) else {}
    layers = context.get("layers") if isinstance(context.get("layers"), dict) else {}
    recent = layers.get("recent_messages") if isinstance(layers.get("recent_messages"), list) else []
    lines = [
        "# Rolling Summary",
        "",
        "## Current Understanding",
        "",
        f"- Topic: {room.get('topic') or context.get('topic') or 'peer room'}",
        f"- Recent messages considered: {len(recent)}",
        "",
        "## Open Questions",
        "",
        "- Pending peer-agent finalization.",
    ]
    return "\n".join(lines)


def _room_summary(room: dict[str, Any]) -> dict[str, Any]:
    return {
        "room_id": room.get("room_id"),
        "topic": room.get("topic"),
        "initiator_node_id": room.get("initiator_node_id"),
        "participants": room.get("participants", []),
        "status": room.get("status"),
        "message_count": len(room.get("messages", [])) if isinstance(room.get("messages"), list) else 0,
    }


def _deadline_at(timeout_seconds: float) -> str:
    return datetime.fromtimestamp(time.time() + max(0.0, timeout_seconds), tz=timezone.utc).isoformat()


def _deadline_expired(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _has_verified_transport(metadata: dict[str, Any]) -> bool:
    auth = metadata.get("transport_auth") if isinstance(metadata.get("transport_auth"), dict) else {}
    if not str(auth.get("auth") or "").startswith("netd:"):
        return False
    return bool(auth.get("authenticated") or str(auth.get("remote_peer_id") or "").strip())


def _targets_peer(message: dict[str, Any], metadata: dict[str, Any], node_id: str) -> bool:
    recipients = _recipient_set(message.get("to_node_ids") or message.get("to"))
    target_peer = str(metadata.get("target_peer_id") or "").strip()
    if target_peer:
        return target_peer == node_id
    return node_id in recipients


def _recipient_set(value: Any) -> set[str]:
    if value is None or value == "":
        return set()
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0.0, min(1.0, parsed))


def _require_text(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text
