"""Registry of legacy import surfaces preserved during staged refactors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompatibilityImport:
    """A legacy module path and its current canonical owner."""

    legacy_module: str
    canonical_module: str
    boundary: str
    note: str = ""


COMPATIBILITY_IMPORTS: tuple[CompatibilityImport, ...] = (
    CompatibilityImport("agent_memory_orchestrator.cli", "agent_memory_orchestrator.runtime.cli.main", "runtime"),
    CompatibilityImport("agent_memory_orchestrator.daemon", "agent_memory_orchestrator.runtime.daemon.server", "runtime"),
    CompatibilityImport(
        "agent_memory_orchestrator.daemon_client",
        "agent_memory_orchestrator.runtime.daemon.client",
        "runtime",
    ),
    CompatibilityImport(
        "agent_memory_orchestrator.mcp_server",
        "agent_memory_orchestrator.runtime.mcp.server",
        "runtime",
    ),
    CompatibilityImport(
        "agent_memory_orchestrator.mcp_memory_tools",
        "agent_memory_orchestrator.runtime.mcp.tools",
        "runtime",
    ),
    CompatibilityImport("agent_memory_orchestrator.graph_service", "agent_memory_orchestrator.graph.service", "graph"),
    CompatibilityImport("agent_memory_orchestrator.graph_store", "agent_memory_orchestrator.graph.store", "graph"),
    CompatibilityImport("agent_memory_orchestrator.graph_cache", "agent_memory_orchestrator.graph.cache", "graph"),
    CompatibilityImport("agent_memory_orchestrator.graph_merge", "agent_memory_orchestrator.graph.merge", "graph"),
    CompatibilityImport(
        "agent_memory_orchestrator.graph_consolidation",
        "agent_memory_orchestrator.graph.consolidation",
        "graph",
    ),
    CompatibilityImport(
        "agent_memory_orchestrator.graph_diagnostics",
        "agent_memory_orchestrator.graph.diagnostics",
        "graph",
    ),
    CompatibilityImport("agent_memory_orchestrator.session_graph", "agent_memory_orchestrator.graph.session", "graph"),
    CompatibilityImport("agent_memory_orchestrator.raw_evidence", "agent_memory_orchestrator.evidence.raw_store", "evidence"),
    CompatibilityImport("agent_memory_orchestrator.evidence_drain", "agent_memory_orchestrator.evidence.drain", "evidence"),
    CompatibilityImport("agent_memory_orchestrator.evidence_window", "agent_memory_orchestrator.evidence.window", "evidence"),
    CompatibilityImport(
        "agent_memory_orchestrator.graph_triggers",
        "agent_memory_orchestrator.evidence.triggers",
        "evidence",
    ),
    CompatibilityImport("agent_memory_orchestrator.embeddings", "agent_memory_orchestrator.llm.embeddings", "llm"),
    CompatibilityImport("agent_memory_orchestrator.model_manager", "agent_memory_orchestrator.llm.models", "llm"),
    CompatibilityImport("agent_memory_orchestrator.qwen_client", "agent_memory_orchestrator.llm.qwen", "llm"),
    CompatibilityImport("agent_memory_orchestrator.rerankers", "agent_memory_orchestrator.llm.rerankers", "llm"),
    CompatibilityImport("agent_memory_orchestrator.vector_cache", "agent_memory_orchestrator.llm.vector_cache", "llm"),
)


def compatibility_imports(boundary: str | None = None) -> tuple[CompatibilityImport, ...]:
    """Return registered compatibility imports, optionally filtered by boundary."""

    if boundary is None:
        return COMPATIBILITY_IMPORTS
    return tuple(item for item in COMPATIBILITY_IMPORTS if item.boundary == boundary)


def canonical_module_for(legacy_module: str) -> str | None:
    """Return the canonical module path for a registered legacy import."""

    for item in COMPATIBILITY_IMPORTS:
        if item.legacy_module == legacy_module:
            return item.canonical_module
    return None


def legacy_modules(boundary: str | None = None) -> tuple[str, ...]:
    """Return registered legacy module paths, optionally filtered by boundary."""

    return tuple(item.legacy_module for item in compatibility_imports(boundary=boundary))
