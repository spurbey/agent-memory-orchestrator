from __future__ import annotations


def test_application_ports_are_importable_from_package_boundary() -> None:
    from agent_memory_orchestrator.application import ports

    expected = {
        "ConnectorTransportPort",
        "EmbeddingStorePort",
        "EvidenceStorePort",
        "GitPort",
        "GraphStorePort",
        "LlmPort",
        "PeerTransportPort",
        "RetrievalStorePort",
    }

    assert expected.issubset(set(ports.__all__))
    for name in expected:
        assert getattr(ports, name) is not None
