from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "CommitMergeEngine": ".merge",
    "ConsolidationCandidate": ".consolidation",
    "DeterministicGraphConsolidator": ".consolidation",
    "DeterministicGraphExtractor": ".session",
    "GraphBackendUnavailable": ".store",
    "GraphConsolidationResult": ".consolidation",
    "GraphDelta": ".session",
    "GraphEdge": ".store",
    "GraphExtractor": ".session",
    "GraphNode": ".store",
    "GraphRagService": ".service",
    "GraphSearchCache": ".cache",
    "GraphStore": ".store",
    "InMemoryGraphStore": ".store",
    "KuzuGraphStore": ".store",
    "MergeCandidate": ".merge",
    "MergeClassifier": ".merge",
    "QwenGraphExtractor": ".session",
    "QwenMergeClassifier": ".merge",
    "SessionGraphBuilder": ".session",
    "create_graph_service": ".service",
    "debug_drain": ".diagnostics",
    "debug_graph": ".diagnostics",
    "debug_hooks": ".diagnostics",
    "debug_qwen": ".diagnostics",
    "debug_retrieval": ".diagnostics",
    "trigger_preview": ".diagnostics",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module_name = _EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module = import_module(module_name, __name__)
    value = getattr(module, name)
    globals()[name] = value
    return value
