from __future__ import annotations

from agent_memory_orchestrator.extensions.contracts import ConnectorEvent
from agent_memory_orchestrator.extensions.contracts import GraphAlgorithmContext
from agent_memory_orchestrator.extensions.contracts import RerankRequest
from agent_memory_orchestrator.extensions.contracts import RetrievalRequest


def test_extension_contract_dataclasses_are_importable() -> None:
    assert RetrievalRequest(query="why did code change?", repo_id="repo:amo").limit == 10
    assert GraphAlgorithmContext(repo_id="repo:amo").mode == "active"
    assert ConnectorEvent(source="slack", event_type="message", text="hello").source == "slack"
    assert RerankRequest(query="why", candidates=[{"id": "doc:1"}]).limit == 10
