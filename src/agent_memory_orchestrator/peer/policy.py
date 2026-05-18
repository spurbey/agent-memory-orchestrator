from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .models import PeerConfig


@dataclass(slots=True, frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason}


class PeerPolicy:
    def __init__(self, config: PeerConfig) -> None:
        self.config = config

    def decide_invite(self, initiator_node_id: str) -> PolicyDecision:
        initiator_node_id = initiator_node_id.strip()
        if not initiator_node_id:
            return PolicyDecision(False, "initiator_node_id is required")
        if initiator_node_id == self.config.node_id:
            return PolicyDecision(True, "self initiated")
        peer = self.config.peer_by_id(initiator_node_id)
        if peer is None:
            if self.config.auto_join == "trusted_only":
                return PolicyDecision(False, f"initiator is not trusted: {initiator_node_id}")
            return PolicyDecision(True, "auto_join policy allows unknown initiator")
        if peer.trust == "blocked":
            return PolicyDecision(False, f"initiator is blocked: {initiator_node_id}")
        if self.config.auto_join == "trusted_only" and peer.trust != "trusted":
            return PolicyDecision(False, f"initiator is not trusted: {initiator_node_id}")
        return PolicyDecision(True, f"initiator trust is {peer.trust}")

    def llm_projection(self) -> dict[str, Any]:
        return {
            "node_id": self.config.node_id,
            "auto_join": self.config.auto_join,
            "share_boundary": self.config.share_boundary(),
            "capabilities": list(self.config.capabilities),
            "raw_evidence": self.config.raw_evidence,
        }
