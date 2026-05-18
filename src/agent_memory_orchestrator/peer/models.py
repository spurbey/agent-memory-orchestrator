from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


DEFAULT_CAPABILITIES = ("graph_retrieval", "memory_search")


@dataclass(slots=True, frozen=True)
class PeerNode:
    node_id: str
    base_url: str = ""
    peer_id: str = ""
    multiaddrs: tuple[str, ...] = field(default_factory=tuple)
    relay_addrs: tuple[str, ...] = field(default_factory=tuple)
    rendezvous_addr: str = ""
    rendezvous_namespace: str = ""
    display_name: str = ""
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    trust: str = "trusted"
    shared_secret_env: str = ""

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PeerNode":
        return cls(
            node_id=str(payload.get("node_id") or "").strip(),
            base_url=str(payload.get("base_url") or "").strip().rstrip("/"),
            peer_id=str(payload.get("peer_id") or "").strip(),
            multiaddrs=tuple(str(item).strip() for item in payload.get("multiaddrs", []) if str(item).strip()),
            relay_addrs=tuple(str(item).strip() for item in payload.get("relay_addrs", []) if str(item).strip()),
            rendezvous_addr=str(payload.get("rendezvous_addr") or "").strip(),
            rendezvous_namespace=str(payload.get("rendezvous_namespace") or "").strip(),
            display_name=str(payload.get("display_name") or "").strip(),
            capabilities=tuple(str(item).strip() for item in payload.get("capabilities", []) if str(item).strip()),
            trust=str(payload.get("trust") or "trusted").strip() or "trusted",
            shared_secret_env=str(payload.get("shared_secret_env") or "").strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "base_url": self.base_url,
            "peer_id": self.peer_id,
            "multiaddrs": list(self.multiaddrs),
            "relay_addrs": list(self.relay_addrs),
            "rendezvous_addr": self.rendezvous_addr,
            "rendezvous_namespace": self.rendezvous_namespace,
            "display_name": self.display_name,
            "capabilities": list(self.capabilities),
            "trust": self.trust,
            "shared_secret_env": self.shared_secret_env,
        }


@dataclass(slots=True, frozen=True)
class PeerConfig:
    node_id: str
    display_name: str = ""
    transport: str = "libp2p"
    auto_join: str = "trusted_only"
    share_summaries: bool = True
    share_citations: bool = True
    raw_evidence: str = "approval_required"
    max_active_rooms: int = 4
    capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES
    peers: tuple[PeerNode, ...] = field(default_factory=tuple)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "PeerConfig":
        return cls(
            node_id=str(payload.get("node_id") or "local-amo").strip() or "local-amo",
            display_name=str(payload.get("display_name") or "").strip(),
            transport=str(payload.get("transport") or "libp2p").strip() or "libp2p",
            auto_join=str(payload.get("auto_join") or "trusted_only").strip() or "trusted_only",
            share_summaries=bool(payload.get("share_summaries", True)),
            share_citations=bool(payload.get("share_citations", True)),
            raw_evidence=str(payload.get("raw_evidence") or "approval_required").strip() or "approval_required",
            max_active_rooms=int(payload.get("max_active_rooms") or 4),
            capabilities=tuple(
                str(item).strip() for item in payload.get("capabilities", DEFAULT_CAPABILITIES) if str(item).strip()
            ),
            peers=tuple(PeerNode.from_dict(item) for item in payload.get("peers", []) if isinstance(item, dict)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "display_name": self.display_name,
            "transport": self.transport,
            "auto_join": self.auto_join,
            "share_summaries": self.share_summaries,
            "share_citations": self.share_citations,
            "raw_evidence": self.raw_evidence,
            "max_active_rooms": self.max_active_rooms,
            "capabilities": list(self.capabilities),
            "peers": [peer.to_dict() for peer in self.peers],
        }

    def share_boundary(self) -> str:
        parts = []
        if self.share_summaries:
            parts.append("summaries allowed")
        if self.share_citations:
            parts.append("citations allowed")
        parts.append(f"raw evidence {self.raw_evidence.replace('_', ' ')}")
        return "; ".join(parts)

    def peer_by_id(self, node_id: str) -> PeerNode | None:
        for peer in self.peers:
            if peer.node_id == node_id:
                return peer
        return None

    def with_peer(self, peer: PeerNode) -> "PeerConfig":
        peers = [item for item in self.peers if item.node_id != peer.node_id]
        peers.append(peer)
        return PeerConfig(
            node_id=self.node_id,
            display_name=self.display_name,
            transport=self.transport,
            auto_join=self.auto_join,
            share_summaries=self.share_summaries,
            share_citations=self.share_citations,
            raw_evidence=self.raw_evidence,
            max_active_rooms=self.max_active_rooms,
            capabilities=self.capabilities,
            peers=tuple(sorted(peers, key=lambda item: item.node_id)),
        )
