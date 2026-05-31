from __future__ import annotations


def test_stage4_domain_peer_boundary_exports_existing_peer_contracts() -> None:
    from agent_memory_orchestrator.domain.peer import PeerConfig
    from agent_memory_orchestrator.domain.peer import PeerContextPack
    from agent_memory_orchestrator.domain.peer import PeerMessage
    from agent_memory_orchestrator.domain.peer import PeerNode
    from agent_memory_orchestrator.domain.peer import PeerPolicy
    from agent_memory_orchestrator.domain.peer import normalize_recipients
    from agent_memory_orchestrator.peer import PeerConfig as LegacyPeerConfig
    from agent_memory_orchestrator.peer import PeerContextPack as LegacyPeerContextPack
    from agent_memory_orchestrator.peer import PeerMessage as LegacyPeerMessage
    from agent_memory_orchestrator.peer import PeerNode as LegacyPeerNode
    from agent_memory_orchestrator.peer.policy import PeerPolicy as LegacyPeerPolicy

    assert PeerConfig is LegacyPeerConfig
    assert PeerContextPack is LegacyPeerContextPack
    assert PeerMessage is LegacyPeerMessage
    assert PeerNode is LegacyPeerNode
    assert PeerPolicy is LegacyPeerPolicy
    assert normalize_recipients(["peer-a", "", "peer-b"]) == ("peer-a", "peer-b")
