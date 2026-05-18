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
    if role == "initiator":
        recent = tuple([item for item in messages if is_conversation_message(item)][-3:])
    else:
        recent = tuple(_pairwise_messages(messages, initiator=initiator, peer=viewer_node_id)[-4:])
    pack = PeerContextPack(
        room_id=room_id,
        viewer_node_id=viewer_node_id,
        role=role,
        room_md=str(room.get("room_md") or ""),
        rolling_summary_md=str(room.get("rolling_summary_md") or ""),
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
        recent_messages=pack.recent_messages,
        policy_projection=pack.policy_projection,
        context_text=_render_context_text(pack),
    )


def _pairwise_messages(messages: list[dict[str, Any]], *, initiator: str, peer: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for message in messages:
        if not is_conversation_message(message):
            continue
        sender = str(message.get("from_node_id") or message.get("from") or "")
        recipients = normalize_recipients(message.get("to_node_ids") or message.get("to"))
        if sender == initiator and peer in recipients:
            out.append(message)
        elif sender == peer and (initiator in recipients or not recipients):
            out.append(message)
    return out


def _render_context_text(pack: PeerContextPack) -> str:
    recent_lines = []
    for message in pack.recent_messages:
        sender = message.get("from_node_id") or message.get("from") or "unknown"
        message_type = message.get("type") or "peer_message"
        content = str(message.get("content") or "").strip()
        citations = message.get("citations") or []
        citation_text = f" citations={citations}" if citations else ""
        recent_lines.append(f"- [{message_type}] {sender}: {content}{citation_text}")
    recent_text = "\n".join(recent_lines) if recent_lines else "- No recent scoped exchanges."
    return (
        "AMO Peer Room Context\n\n"
        "Layer 1 - Room Brief\n"
        f"{pack.room_md.strip()}\n\n"
        "Layer 2 - Rolling Summary\n"
        f"{pack.rolling_summary_md.strip()}\n\n"
        f"Layer 3 - Recent {'Room' if pack.role == 'initiator' else 'Pairwise'} Exchanges\n"
        f"{recent_text}\n\n"
        "Policy Projection\n"
        f"- viewer_node_id: {pack.viewer_node_id}\n"
        f"- role: {pack.role}\n"
        f"- share_boundary: {pack.policy_projection.get('share_boundary')}\n"
        "- Do not expose raw evidence unless policy explicitly allows it.\n"
    )
