from __future__ import annotations


def test_peer_domain_peer_boundary_exports_existing_peer_contracts() -> None:
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


def test_peer_application_and_infrastructure_peer_boundaries_are_importable() -> None:
    from agent_memory_orchestrator.application.services import PeerAgentService
    from agent_memory_orchestrator.infrastructure.peer_netd import PeerNetdClient
    from agent_memory_orchestrator.infrastructure.peer_netd import PeerNetdLaunchOptions
    from agent_memory_orchestrator.infrastructure.peer_netd import PeerNetdRuntime
    from agent_memory_orchestrator.infrastructure.peer_netd import PeerNetdServiceOptions
    from agent_memory_orchestrator.infrastructure.peer_netd import install_service_plan
    from agent_memory_orchestrator.peer.agent import PeerAgentService as LegacyPeerAgentService
    from agent_memory_orchestrator.peer.netd_client import PeerNetdClient as LegacyPeerNetdClient
    from agent_memory_orchestrator.peer.netd_runtime import PeerNetdLaunchOptions as LegacyPeerNetdLaunchOptions
    from agent_memory_orchestrator.peer.netd_runtime import PeerNetdRuntime as LegacyPeerNetdRuntime
    from agent_memory_orchestrator.peer.netd_service import PeerNetdServiceOptions as LegacyPeerNetdServiceOptions
    from agent_memory_orchestrator.peer.netd_service import install_service_plan as legacy_install_service_plan

    assert PeerAgentService is LegacyPeerAgentService
    assert PeerNetdClient is LegacyPeerNetdClient
    assert PeerNetdLaunchOptions is LegacyPeerNetdLaunchOptions
    assert PeerNetdRuntime is LegacyPeerNetdRuntime
    assert PeerNetdServiceOptions is LegacyPeerNetdServiceOptions
    assert install_service_plan is legacy_install_service_plan
