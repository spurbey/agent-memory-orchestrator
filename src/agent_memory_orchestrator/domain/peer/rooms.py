from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import PeerConfig
from .policy import PeerPolicy
from .protocol import is_conversation_message, normalize_recipients


@dataclass(slots=True, frozen=True)
class PeerContextPack:
    room_id: str
    viewer_node_id: str
    role: str
    room_md: str
    rolling_summary_md: str
    room_roster: tuple[dict[str, Any], ...]
    open_questions: tuple[dict[str, Any], ...]
    group_recent_messages: tuple[dict[str, Any], ...]
    pairwise_recent_messages: tuple[dict[str, Any], ...]
    recent_messages: tuple[dict[str, Any], ...]
    policy_projection: dict[str, Any]
    context_text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "room_id": self.room_id,
            "viewer_node_id": self.viewer_node_id,
            "role": self.role,
            "layers": {
                "room_md": self.room_md,
                "rolling_summary_md": self.rolling_summary_md,
                "room_roster": list(self.room_roster),
                "open_questions": list(self.open_questions),
                "group_recent_messages": list(self.group_recent_messages),
                "pairwise_recent_messages": list(self.pairwise_recent_messages),
                "recent_messages": list(self.recent_messages),
            },
            "policy_projection": self.policy_projection,
            "context_text": self.context_text,
        }


def build_context_pack(*, room: dict[str, Any], viewer_node_id: str, config: PeerConfig) -> PeerContextPack:
    room_id = str(room.get("room_id") or "")
    initiator = str(room.get("initiator_node_id") or "")
    viewer_node_id = viewer_node_id.strip() or config.node_id
    role = "initiator" if viewer_node_id == initiator else "peer"
    messages = [item for item in room.get("messages", []) if isinstance(item, dict)]
    roster = tuple(_participant_roster(room=room, viewer_node_id=viewer_node_id, config=config))
    group_recent = tuple(_compact_message(item) for item in _group_visible_messages(messages)[-4:])
    pairwise_recent = tuple(
        _compact_message(item) for item in _pairwise_messages(messages, initiator=initiator, peer=viewer_node_id)[-4:]
    )
    open_questions = tuple(
        _open_questions(messages, viewer_node_id=viewer_node_id, initiator=initiator, role=role)[-6:]
    )
    if role == "initiator":
        recent = group_recent[-3:] or tuple(_compact_message(item) for item in messages if is_conversation_message(item))[-3:]
    else:
        recent = pairwise_recent
    pack = PeerContextPack(
        room_id=room_id,
        viewer_node_id=viewer_node_id,
        role=role,
        room_md=str(room.get("room_md") or ""),
        rolling_summary_md=str(room.get("rolling_summary_md") or ""),
        room_roster=roster,
        open_questions=open_questions,
        group_recent_messages=group_recent,
        pairwise_recent_messages=pairwise_recent,
        recent_messages=recent,
        policy_projection=PeerPolicy(config).llm_projection(),
        context_text="",
    )
    return PeerContextPack(
        room_id=pack.room_id,
        viewer_node_id=pack.viewer_node_id,
        role=pack.role,
        room_md=pack.room_md,
        rolling_summary_md=pack.rolling_summary_md,
        room_roster=pack.room_roster,
        open_questions=pack.open_questions,
        group_recent_messages=pack.group_recent_messages,
        pairwise_recent_messages=pack.pairwise_recent_messages,
        recent_messages=pack.recent_messages,
        policy_projection=pack.policy_projection,
        context_text=_render_context_text(pack),
    )


def _participant_roster(*, room: dict[str, Any], viewer_node_id: str, config: PeerConfig) -> list[dict[str, Any]]:
    initiator = str(room.get("initiator_node_id") or "")
    rows: list[dict[str, Any]] = []
    for participant in room.get("participants", []):
        node_id = str(participant).strip()
        if not node_id:
            continue
        peer = config.peer_by_id(node_id)
        is_self = node_id == viewer_node_id
        capabilities: tuple[str, ...]
        if is_self:
            capabilities = config.capabilities
        elif peer is not None:
            capabilities = peer.capabilities
        else:
            capabilities = ()
        rows.append(
            {
                "node_id": node_id,
                "role": "initiator" if node_id == initiator else "peer",
                "is_self": is_self,
                "display_name": config.display_name if is_self else (peer.display_name if peer else ""),
                "capabilities": list(capabilities[:6]),
                "trust": "self" if is_self else (peer.trust if peer else "unknown"),
            }
        )
    return rows


def _group_visible_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if _is_group_visible(message):
            out.append(message)
    return out


def _is_group_visible(message: dict[str, Any]) -> bool:
    if not is_conversation_message(message):
        return False
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    if metadata.get("local_only"):
        return False
    recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
    audience = str(metadata.get("audience") or "").strip().lower()
    return audience == "group" or not recipients or len(recipients) > 1


def _pairwise_messages(messages: list[dict[str, Any]], *, initiator: str, peer: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if not is_conversation_message(message):
            continue
        if _is_group_visible(message):
            continue
        sender = str(message.get("from_node_id") or message.get("from") or "")
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if sender == initiator and peer in recipients:
            out.append(message)
        elif sender == peer and (initiator in recipients or not recipients):
            out.append(message)
    return out


def _open_questions(
    messages: list[dict[str, Any]],
    *,
    viewer_node_id: str,
    initiator: str,
    role: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if str(message.get("type") or "") != "context_request":
            continue
        sender = str(message.get("from_node_id") or message.get("from") or "")
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if role != "initiator" and not _is_group_visible(message) and sender != viewer_node_id and viewer_node_id not in recipients:
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        query = str(metadata.get("query") or message.get("content") or "").strip()
        if not query:
            continue
        gaps = metadata.get("open_gaps") if isinstance(metadata.get("open_gaps"), list) else []
        out.append(
            {
                "request_id": str(metadata.get("request_id") or message.get("message_id") or ""),
                "from": sender,
                "to": list(recipients),
                "query": _clip(query, 320),
                "open_gaps": [_clip(str(gap), 160) for gap in gaps[:5] if str(gap).strip()],
            }
        )
    return out


def _compact_message(message: dict[str, Any]) -> dict[str, Any]:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    compact_metadata = {
        key: metadata[key]
        for key in ("request_id", "parent_message_id", "mode", "answer_grade", "audience", "target_peer_id")
        if key in metadata
    }
    return {
        "message_id": str(message.get("message_id") or ""),
        "type": str(message.get("type") or ""),
        "from_node_id": str(message.get("from_node_id") or message.get("from") or ""),
        "to_node_ids": list(normalize_recipients(message.get("to_node_ids") or message.get("to"))),
        "content": _clip(str(message.get("content") or ""), 800),
        "citations": list(message.get("citations") or [])[:5] if isinstance(message.get("citations"), list) else [],
        "confidence": message.get("confidence"),
        "metadata": compact_metadata,
    }


def _render_context_text(pack: PeerContextPack) -> str:
    roster_lines = []
    for participant in pack.room_roster:
        self_marker = " self" if participant.get("is_self") else ""
        caps = participant.get("capabilities") or []
        caps_text = f" caps={','.join(caps)}" if caps else ""
        roster_lines.append(
            f"- {participant.get('node_id')} role={participant.get('role')}{self_marker}{caps_text}"
        )
    roster_text = "\n".join(roster_lines) if roster_lines else "- No roster."
    question_lines = []
    for question in pack.open_questions:
        to_text = ",".join(question.get("to") or [])
        gap_text = f" gaps={question.get('open_gaps')}" if question.get("open_gaps") else ""
        question_lines.append(f"- {question.get('request_id')}: {question.get('query')} to={to_text}{gap_text}")
    question_text = "\n".join(question_lines) if question_lines else "- No open questions."
    group_text = _render_messages(pack.group_recent_messages, empty="- No recent group-visible exchanges.")
    pairwise_text = _render_messages(pack.pairwise_recent_messages, empty="- No recent tagged peer exchange.")
    recent_text = _render_messages(pack.recent_messages, empty="- No recent scoped exchanges.")
    return (
        "AMO Peer Room Context\n\n"
        "Layer 1 - Room Brief\n"
        f"{pack.room_md.strip()}\n\n"
        "Room Roster\n"
        f"{roster_text}\n\n"
        "Layer 2 - Rolling Summary\n"
        f"{pack.rolling_summary_md.strip()}\n\n"
        "Open Questions\n"
        f"{question_text}\n\n"
        "Layer 3A - Recent Group-visible Exchanges\n"
        f"{group_text}\n\n"
        "Layer 3B - Recent Tagged Peer Exchanges\n"
        f"{pairwise_text}\n\n"
        f"Active Scoped View ({pack.role})\n"
        f"{recent_text}\n\n"
        "Policy Projection\n"
        f"- viewer_node_id: {pack.viewer_node_id}\n"
        f"- role: {pack.role}\n"
        f"- share_boundary: {pack.policy_projection.get('share_boundary')}\n"
        "- Do not expose raw evidence unless policy explicitly allows it.\n"
    )


def _render_messages(messages: tuple[dict[str, Any], ...], *, empty: str) -> str:
    lines = []
    for message in messages:
        sender = message.get("from_node_id") or message.get("from") or "unknown"
        message_type = message.get("type") or "peer_message"
        content = str(message.get("content") or "").strip()
        recipients = message.get("to_node_ids") or []
        to_text = f" -> {','.join(recipients)}" if recipients else ""
        citations = message.get("citations") or []
        citation_text = f" citations={citations}" if citations else ""
        lines.append(f"- [{message_type}] {sender}{to_text}: {content}{citation_text}")
    return "\n".join(lines) if lines else empty


def _clip(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."
