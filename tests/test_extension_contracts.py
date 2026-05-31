from __future__ import annotations

from agent_memory_orchestrator.extensions.contracts import ConnectorEvent
from agent_memory_orchestrator.extensions.contracts import GraphAlgorithmContext
from agent_memory_orchestrator.extensions.contracts import LocalAgentSkillRequest
from agent_memory_orchestrator.extensions.contracts import RerankRequest
from agent_memory_orchestrator.extensions.contracts import RetrievalRequest
from agent_memory_orchestrator.extensions.loader import LOCAL_EXTENSION_DIRS
from agent_memory_orchestrator.extensions.loader import discover_extension_paths
from agent_memory_orchestrator.extensions.registry import ExtensionDescriptor
from agent_memory_orchestrator.extensions.registry import ExtensionRegistry


def test_extension_contract_dataclasses_are_importable() -> None:
    assert RetrievalRequest(query="why did code change?", repo_id="repo:amo").limit == 10
    assert GraphAlgorithmContext(repo_id="repo:amo").mode == "active"
    assert ConnectorEvent(source="slack", event_type="message", text="hello").source == "slack"
    assert RerankRequest(query="why", candidates=[{"id": "doc:1"}]).limit == 10
    assert LocalAgentSkillRequest(task="review this patch").task == "review this patch"


def test_extension_registry_tracks_local_only_algorithms() -> None:
    registry = ExtensionRegistry()
    extension = object()
    descriptor = ExtensionDescriptor(name="local-reranker", extension_type="reranker", version="dev")

    registry.register(extension, descriptor)

    assert registry.get("reranker", "local-reranker") is extension
    assert registry.descriptors(extension_type="reranker") == [descriptor]
    assert registry.descriptors(extension_type="graph") == []


def test_extension_loader_discovers_private_paths_without_importing(tmp_path) -> None:
    local_root = tmp_path / ".local-extensions"
    good = local_root / "custom-reranker"
    ignored = local_root / "__pycache__"
    good.mkdir(parents=True)
    ignored.mkdir()

    paths = discover_extension_paths(tmp_path)

    assert paths == [good]
    assert ".private-extensions" in LOCAL_EXTENSION_DIRS
