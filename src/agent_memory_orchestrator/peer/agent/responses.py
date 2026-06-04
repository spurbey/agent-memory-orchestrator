from __future__ import annotations

from typing import Any

from .schemas import CONTEXT_RESPONSE
from .service_utils import _clamp_float


def is_finalizable_response(response: dict[str, Any]) -> bool:
    if str(response.get("content") or "").strip():
        return True
    if response.get("support") or response.get("citations"):
        return True
    bundle = response.get("retrieval_bundle") if isinstance(response.get("retrieval_bundle"), dict) else {}
    answer = bundle.get("answer") if isinstance(bundle.get("answer"), dict) else {}
    return bool(str(answer.get("text") or "").strip())


def peer_responses(room: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for message in room.get("messages", []):
        if str(message.get("type") or "") != CONTEXT_RESPONSE:
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        response = {
            "message_id": message.get("message_id", ""),
            "source_peer": message.get("from_node_id") or message.get("from") or "",
            "content": message.get("content", ""),
            "confidence": message.get("confidence", 0.0),
            "citations": message.get("citations", []),
            "mode": metadata.get("mode", ""),
            "answer_grade": bool(metadata.get("answer_grade")),
            "quality": metadata.get("quality") if isinstance(metadata.get("quality"), dict) else {},
            "support": metadata.get("support") if isinstance(metadata.get("support"), list) else [],
            "retrieval_bundle": metadata.get("retrieval_bundle") if isinstance(metadata.get("retrieval_bundle"), dict) else {},
            "request_id": metadata.get("request_id", ""),
        }
        response["finalizable"] = bool(metadata.get("finalizable", is_finalizable_response(response)))
        rows.append(response)
    return rows


def best_finalizable_response(responses: list[dict[str, Any]], *, strong_confidence: float) -> dict[str, Any] | None:
    del strong_confidence
    for response in responses:
        if is_finalizable_response(response):
            return response
    return None


def best_response(responses: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not responses:
        return None
    return sorted(
        responses,
        key=lambda item: (
            is_finalizable_response(item),
            bool(item.get("support") or item.get("citations")),
            bool(item.get("answer_grade")),
            _clamp_float(item.get("confidence"), default=0.0),
        ),
        reverse=True,
    )[0]
