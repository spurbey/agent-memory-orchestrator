from __future__ import annotations

import pytest

from agent_memory_orchestrator.peer.cards import build_peer_card, peer_from_card
from agent_memory_orchestrator.peer.invites import build_peer_invite
from agent_memory_orchestrator.peer.invites import decode_invite_code
from agent_memory_orchestrator.peer.invites import encode_invite_code
from agent_memory_orchestrator.peer.invites import invite_token_hash
from agent_memory_orchestrator.peer.invites import parse_peer_invite
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


def test_peer_invite_code_round_trip() -> None:
    card = build_peer_card(
        config=PeerConfig(node_id="node-a", display_name="Node A"),
        base_url="http://127.0.0.1:8787",
    )
    invite = build_peer_invite(card=card, trust="trusted", label="Node A invite")

    decoded = decode_invite_code(encode_invite_code(invite))
    parsed = parse_peer_invite(decoded)

    assert decoded["invite_id"] == invite["invite_id"]
    assert parsed["card"]["node_id"] == "node-a"
    assert parsed["trust"] == "trusted"
    assert parsed["invite_token"]
    assert invite_token_hash(parsed["invite_token"]) == parsed["token_proof"]


def test_peer_invite_rejects_card_hash_mismatch() -> None:
    card = build_peer_card(config=PeerConfig(node_id="node-a"), base_url="http://127.0.0.1:8787")
    invite = build_peer_invite(card=card)
    invite["card"]["node_id"] = "node-b"

    with pytest.raises(ValueError, match="hash mismatch"):
        parse_peer_invite(invite)
