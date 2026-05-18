from __future__ import annotations

from typing import Any

from agent_memory_orchestrator.peer.models import PeerConfig, PeerNode

PEER_CARD_VERSION = 1


def build_peer_card(
    *,
    config: PeerConfig,
    netd_health: dict[str, Any] | None = None,
    base_url: str = "",
    rendezvous_addr: str = "",
    rendezvous_namespace: str = "",
) -> dict[str, Any]:
    health = netd_health or {}
    peer_id = str(health.get("peer_id") or "").strip()
    multiaddrs = [str(item).strip() for item in health.get("listen_addrs", []) if str(item).strip()]
    relay_addrs = [str(item).strip() for item in health.get("relay_addrs", []) if str(item).strip()]
    return {
        "amo_peer_card_version": PEER_CARD_VERSION,
        "node_id": config.node_id,
        "display_name": config.display_name,
        "capabilities": list(config.capabilities),
        "transport": "libp2p",
        "base_url": base_url.strip().rstrip("/"),
        "peer_id": peer_id,
        "multiaddrs": multiaddrs,
        "relay_addrs": relay_addrs,
        "rendezvous_addr": rendezvous_addr.strip(),
        "rendezvous_namespace": rendezvous_namespace.strip(),
    }


def peer_from_card(
    card: dict[str, Any],
    *,
    trust: str = "trusted",
    shared_secret_env: str = "",
) -> PeerNode:
    if int(card.get("amo_peer_card_version") or 0) != PEER_CARD_VERSION:
        raise ValueError("unsupported AMO peer card version")
    node_id = str(card.get("node_id") or "").strip()
    if not node_id:
        raise ValueError("peer card node_id is required")
    peer = PeerNode(
        node_id=node_id,
        base_url=str(card.get("base_url") or "").strip().rstrip("/"),
        peer_id=str(card.get("peer_id") or "").strip(),
        multiaddrs=tuple(str(item).strip() for item in card.get("multiaddrs", []) if str(item).strip()),
        relay_addrs=tuple(str(item).strip() for item in card.get("relay_addrs", []) if str(item).strip()),
        rendezvous_addr=str(card.get("rendezvous_addr") or "").strip(),
        rendezvous_namespace=str(card.get("rendezvous_namespace") or "").strip(),
        display_name=str(card.get("display_name") or "").strip(),
        capabilities=tuple(str(item).strip() for item in card.get("capabilities", []) if str(item).strip()),
        trust=trust,
        shared_secret_env=shared_secret_env,
    )
    if not any((peer.base_url, peer.peer_id, peer.multiaddrs, peer.relay_addrs, peer.rendezvous_addr)):
        raise ValueError("peer card does not contain a usable transport address")
    return peer
