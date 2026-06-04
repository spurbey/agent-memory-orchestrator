from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


SYSTEM_MESSAGE_TYPES = {"room_created", "room_invite_received", "summary_update"}


def normalize_recipients(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


def normalize_citations(value: Any) -> tuple[str, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return (str(value).strip(),) if str(value).strip() else ()


@dataclass(slots=True, frozen=True)
class PeerMessage:
    room_id: str
    message_type: str
    from_node_id: str
    to_node_ids: tuple[str, ...] = field(default_factory=tuple)
    content: str = ""
    confidence: float | None = None
    citations: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)
    message_id: str = ""
    created_at: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "PeerMessage":
        confidence = payload.get("confidence")
        try:
            normalized_confidence = float(confidence) if confidence is not None and confidence != "" else None
        except (TypeError, ValueError):
            normalized_confidence = None
        return cls(
            room_id=str(payload.get("room_id") or "").strip(),
            message_type=str(payload.get("type") or payload.get("message_type") or "peer_message").strip()
            or "peer_message",
            from_node_id=str(payload.get("from") or payload.get("from_node_id") or "").strip(),
            to_node_ids=normalize_recipients(payload.get("to") or payload.get("to_node_ids")),
            content=str(payload.get("content") or ""),
            confidence=normalized_confidence,
            citations=normalize_citations(payload.get("citations")),
            metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
            message_id=str(payload.get("message_id") or "").strip(),
            created_at=str(payload.get("created_at") or "").strip(),
        )

    def to_record(self) -> dict[str, Any]:
        record: dict[str, Any] = {
            "message_id": self.message_id or f"msg_{uuid4().hex}",
            "type": self.message_type,
            "from": self.from_node_id,
            "from_node_id": self.from_node_id,
            "to": list(self.to_node_ids),
            "to_node_ids": list(self.to_node_ids),
            "content": self.content,
            "citations": list(self.citations),
            "metadata": dict(self.metadata),
        }
        if self.confidence is not None:
            record["confidence"] = self.confidence
        if self.created_at:
            record["created_at"] = self.created_at
        return record


def is_conversation_message(message: dict[str, Any]) -> bool:
    return str(message.get("type") or "") not in SYSTEM_MESSAGE_TYPES
