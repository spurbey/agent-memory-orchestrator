from __future__ import annotations

import hashlib
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
    active_recent_messages: tuple[dict[str, Any], ...]
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
                "active_recent_messages": list(self.active_recent_messages),
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
    rolling_summary_md = _rolling_summary_for_view(
        room=room,
        messages=messages,
        viewer_node_id=viewer_node_id,
        initiator=initiator,
        role=role,
    )
    group_recent = tuple(_compact_message(item) for item in _group_visible_messages(messages)[-4:])
    active_recent = tuple(_active_room_discussion_messages(messages, initiator=initiator)[-2:])
    if role == "initiator":
        pairwise_recent = tuple(_initiator_orchestration_messages(messages, initiator=initiator)[-8:])
    else:
        pairwise_recent = tuple(
            _compact_message(item) for item in _pairwise_messages(messages, initiator=initiator, peer=viewer_node_id)[-4:]
        )
    open_questions = tuple(
        _open_questions(messages, viewer_node_id=viewer_node_id, initiator=initiator, role=role)[-6:]
    )
    if role == "initiator":
        recent = pairwise_recent
    else:
        recent = pairwise_recent
    pack = PeerContextPack(
        room_id=room_id,
        viewer_node_id=viewer_node_id,
        role=role,
        room_md=str(room.get("room_md") or ""),
        rolling_summary_md=rolling_summary_md,
        room_roster=roster,
        open_questions=open_questions,
        group_recent_messages=group_recent,
        active_recent_messages=active_recent,
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
        active_recent_messages=pack.active_recent_messages,
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


def _rolling_summary_for_view(
    *,
    room: dict[str, Any],
    messages: list[dict[str, Any]],
    viewer_node_id: str,
    initiator: str,
    role: str,
) -> str:
    local_summary = str(room.get("rolling_summary_md") or "")
    if role == "initiator":
        return local_summary
    shared_summary = _latest_shared_summary(messages, viewer_node_id=viewer_node_id, initiator=initiator)
    return shared_summary or local_summary


def _latest_shared_summary(messages: list[dict[str, Any]], *, viewer_node_id: str, initiator: str) -> str:
    for message in reversed(messages):
        if str(message.get("type") or "") != "context_request":
            continue
        if str(message.get("from_node_id") or message.get("from") or "") != initiator:
            continue
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if viewer_node_id not in recipients:
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        summary_md = str(metadata.get("room_summary_md") or "").strip()
        if summary_md:
            return summary_md
    return ""


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


def _initiator_orchestration_messages(messages: list[dict[str, Any]], *, initiator: str) -> list[dict[str, Any]]:
    """Project transport messages into the initiator's peer-orchestration view.

    Transport may carry one context_request per peer for delivery/idempotency.
    The initiator context should show one logical question with all tagged peers,
    followed by peer responses, rather than duplicate request prose.
    """
    out: list[dict[str, Any]] = []
    request_groups: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not is_conversation_message(message):
            continue
        if _is_group_visible(message):
            continue
        message_type = str(message.get("type") or "")
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        sender = str(message.get("from_node_id") or message.get("from") or "")
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if message_type == "context_request" and sender == initiator:
            key = _logical_request_key(message, metadata)
            group = request_groups.get(key)
            if group is None:
                group = _compact_request_group(message, metadata)
                request_groups[key] = group
                out.append(group)
            _merge_request_group(group, recipients, metadata)
            continue
        if message_type == "context_response" and sender != initiator and (initiator in recipients or not recipients):
            out.append(_compact_message(message))
            continue
        if message_type == "peer_message" and (sender == initiator or initiator in recipients or not recipients):
            out.append(_compact_message(message))
    return out


def _active_room_discussion_messages(messages: list[dict[str, Any]], *, initiator: str) -> list[dict[str, Any]]:
    """Return the short room-wide discussion window, independent of viewer.

    Layer 3A is intentionally not room metadata and not arbitrary notes. It is
    the latest initiator-led request/response flow so every agent knows what is
    currently being discussed before applying its own Layer 3B pairwise view.
    """

    out: list[dict[str, Any]] = []
    request_groups: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not is_conversation_message(message):
            continue
        if _is_local_only(message):
            continue
        message_type = str(message.get("type") or "")
        if message_type not in {"context_request", "context_response"}:
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        sender = str(message.get("from_node_id") or message.get("from") or "")
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if message_type == "context_request" and sender == initiator:
            key = _logical_request_key(message, metadata)
            group = request_groups.get(key)
            if group is None:
                group = _compact_request_group(message, metadata)
                request_groups[key] = group
                out.append(group)
            _merge_request_group(group, recipients, metadata)
        elif message_type == "context_response" and sender != initiator and (initiator in recipients or not recipients):
            out.append(_compact_message(message))
    return out


def _is_local_only(message: dict[str, Any]) -> bool:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    return bool(metadata.get("local_only"))


def _logical_request_key(message: dict[str, Any], metadata: dict[str, Any]) -> str:
    logical_id = str(metadata.get("logical_request_id") or "").strip()
    if logical_id:
        return f"logical:{logical_id}"
    query = str(metadata.get("query") or message.get("content") or "").strip()
    return f"query:{query}"


def _compact_request_group(message: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    query = str(metadata.get("query") or message.get("content") or "").strip()
    logical_id = str(metadata.get("logical_request_id") or "").strip()
    request_id = str(metadata.get("request_id") or message.get("message_id") or "")
    fallback_id = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return {
        "message_id": f"group_{logical_id or request_id or fallback_id}",
        "type": "context_request_group",
        "from_node_id": str(message.get("from_node_id") or message.get("from") or ""),
        "to_node_ids": [],
        "content": _clip(query, 800),
        "citations": [],
        "confidence": None,
        "metadata": {
            "logical_request_id": logical_id,
            "request_ids": [],
            "request_count": 0,
        },
    }


def _merge_request_group(group: dict[str, Any], recipients: tuple[str, ...], metadata: dict[str, Any]) -> None:
    to_node_ids = list(group.get("to_node_ids") or [])
    for recipient in recipients:
        if recipient not in to_node_ids:
            to_node_ids.append(recipient)
    group["to_node_ids"] = sorted(to_node_ids)
    compact_metadata = group.get("metadata") if isinstance(group.get("metadata"), dict) else {}
    request_ids = list(compact_metadata.get("request_ids") or [])
    request_id = str(metadata.get("request_id") or "").strip()
    if request_id and request_id not in request_ids:
        request_ids.append(request_id)
    compact_metadata["request_ids"] = request_ids
    compact_metadata["request_count"] = len(request_ids) if request_ids else max(len(to_node_ids), 1)
    group["metadata"] = compact_metadata


def _open_questions(
    messages: list[dict[str, Any]],
    *,
    viewer_node_id: str,
    initiator: str,
    role: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    answered_request_ids = _answered_request_ids(messages)
    for message in messages:
        if str(message.get("type") or "") != "context_request":
            continue
        sender = str(message.get("from_node_id") or message.get("from") or "")
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if role != "initiator" and not _is_group_visible(message) and sender != viewer_node_id and viewer_node_id not in recipients:
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        request_id = str(metadata.get("request_id") or message.get("message_id") or "")
        if request_id and request_id in answered_request_ids:
            continue
        query = str(metadata.get("query") or message.get("content") or "").strip()
        if not query:
            continue
        gaps = metadata.get("open_gaps") if isinstance(metadata.get("open_gaps"), list) else []
        key = _logical_request_key(message, metadata) if role == "initiator" else str(metadata.get("request_id") or message.get("message_id") or "")
        row = groups.get(key)
        if row is None:
            row = {
                "request_id": str(metadata.get("request_id") or message.get("message_id") or ""),
                "request_ids": [],
                "from": sender,
                "to": [],
                "query": _clip(query, 320),
                "open_gaps": [],
            }
            groups[key] = row
            out.append(row)
        if request_id and request_id not in row["request_ids"]:
            row["request_ids"].append(request_id)
        for recipient in recipients:
            if recipient not in row["to"]:
                row["to"].append(recipient)
        for gap in gaps[:5]:
            clipped = _clip(str(gap), 160)
            if clipped and clipped not in row["open_gaps"]:
                row["open_gaps"].append(clipped)
        row["to"] = sorted(row["to"])
        row["request_count"] = len(row["request_ids"]) if row["request_ids"] else max(len(row["to"]), 1)
    return out


def _answered_request_ids(messages: list[dict[str, Any]]) -> set[str]:
    answered: set[str] = set()
    for message in messages:
        if str(message.get("type") or "") != "context_response":
            continue
        metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
        for key in ("request_id", "parent_message_id"):
            request_id = str(metadata.get(key) or "").strip()
            if request_id:
                answered.add(request_id)
    return answered


def _compact_message(message: dict[str, Any]) -> dict[str, Any]:
    metadata = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
    compact_metadata = {
        key: metadata[key]
        for key in ("request_id", "parent_message_id", "mode", "answer_grade", "audience", "target_peer_id")
        if key in metadata
    }
    if "logical_request_id" in metadata:
        compact_metadata["logical_request_id"] = metadata["logical_request_id"]
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
    active_text = _render_messages(pack.active_recent_messages, empty="- No active room discussion yet.")
    pairwise_text = _render_messages(pack.pairwise_recent_messages, empty="- No recent tagged peer exchange.")
    pairwise_label = (
        "Layer 3B - Recent Peer-Orchestration Exchanges"
        if pack.role == "initiator"
        else "Layer 3B - Recent Tagged Peer Exchanges"
    )
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
        "Layer 3A - Active Room Discussion\n"
        f"{active_text}\n\n"
        f"{pairwise_label}\n"
        f"{pairwise_text}\n\n"
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
