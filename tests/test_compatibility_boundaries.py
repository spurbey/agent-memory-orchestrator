from __future__ import annotations

from agent_memory_orchestrator.compatibility.graph_service import GraphRagService
from agent_memory_orchestrator.compatibility.old_imports import (
    canonical_module_for,
    compatibility_imports,
    legacy_modules,
)
from agent_memory_orchestrator.compatibility.runtime import cli_main


def test_compatibility_registry_tracks_legacy_runtime_and_graph_imports() -> None:
    assert canonical_module_for("agent_memory_orchestrator.cli") == "agent_memory_orchestrator.runtime.cli.main"
    assert canonical_module_for("agent_memory_orchestrator.graph_service") == "agent_memory_orchestrator.graph.service"
    assert "agent_memory_orchestrator.daemon" in legacy_modules(boundary="runtime")
    assert "agent_memory_orchestrator.graph_service" in legacy_modules(boundary="graph")


def test_compatibility_registry_can_filter_by_boundary() -> None:
    runtime_imports = compatibility_imports(boundary="runtime")

    assert runtime_imports
    assert {item.boundary for item in runtime_imports} == {"runtime"}


def test_compatibility_exports_match_existing_wrappers() -> None:
    from agent_memory_orchestrator import cli as compat_cli
    from agent_memory_orchestrator.graph_service import GraphRagService as RootGraphRagService

    assert cli_main is compat_cli.main
    assert GraphRagService is RootGraphRagService
