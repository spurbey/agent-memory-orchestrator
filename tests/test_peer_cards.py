from __future__ import annotations

import pytest

from agent_memory_orchestrator.peer.cards import build_peer_card, peer_from_card
from agent_memory_orchestrator.peer.models import PeerConfig


def test_peer_card_round_trip_from_netd_health() -> None:
    card = build_peer_card(
        config=PeerConfig(node_id="node-a", display_name="Node A", capabilities=("graph_retrieval",)),
        netd_health={
            "peer_id": "12D3KooWPeer",
            "listen_addrs": ["/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer"],
            "relay_addrs": ["/ip4/relay/tcp/4001/p2p/relay/p2p-circuit/p2p/12D3KooWPeer"],
        },
        rendezvous_addr="/ip4/127.0.0.1/tcp/9000/p2p/12D3KooWRendezvous",
        rendezvous_namespace="amo-team",
    )

    peer = peer_from_card(card, trust="limited", shared_secret_env="AMO_PEER_SECRET")

    assert card["amo_peer_card_version"] == 1
    assert peer.node_id == "node-a"
    assert peer.peer_id == "12D3KooWPeer"
    assert peer.multiaddrs == ("/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer",)
    assert peer.relay_addrs == ("/ip4/relay/tcp/4001/p2p/relay/p2p-circuit/p2p/12D3KooWPeer",)
    assert peer.rendezvous_namespace == "amo-team"
    assert peer.trust == "limited"
    assert peer.shared_secret_env == "AMO_PEER_SECRET"


def test_peer_card_rejects_empty_transport() -> None:
    card = build_peer_card(config=PeerConfig(node_id="node-a"))

    with pytest.raises(ValueError, match="usable transport"):
        peer_from_card(card)
