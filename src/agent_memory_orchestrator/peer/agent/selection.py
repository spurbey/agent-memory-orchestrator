from __future__ import annotations

from agent_memory_orchestrator.peer.models import PeerConfig, PeerNode


def select_context_peers(
    config: PeerConfig,
    *,
    peer_ids: list[str] | None = None,
    max_peers: int,
) -> list[PeerNode]:
    requested = [item.strip() for item in peer_ids or [] if item.strip()]
    requested_set = set(requested)
    by_id = {peer.node_id: peer for peer in config.peers}
    candidates = [by_id[item] for item in requested if item in by_id] if requested else list(config.peers)
    selected: list[PeerNode] = []
    for peer in candidates:
        if requested_set and peer.node_id not in requested_set:
            continue
        if peer.trust != "trusted":
            continue
        if not any((peer.peer_id, peer.base_url, peer.multiaddrs, peer.relay_addrs, peer.rendezvous_addr)):
            continue
        capabilities = set(peer.capabilities)
        if capabilities and not capabilities.intersection({"graph_retrieval", "memory_search"}):
            continue
        selected.append(peer)
        if len(selected) >= max_peers:
            break
    return selected
