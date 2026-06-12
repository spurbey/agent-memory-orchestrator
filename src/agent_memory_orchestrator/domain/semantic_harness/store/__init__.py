"""Storage boundary for the Semantic Harness graph.

The domain works against this boundary so in-memory testing, SQLite ledgers,
and future Kuzu graph storage can share the same apply semantics.
"""

from .apply import GraphDeltaApplyResult
from .apply import apply_graph_update_delta
from .interfaces import EdgeKey
from .interfaces import HarnessGraphStore
from .memory import InMemoryHarnessGraphStore

__all__ = [
    "EdgeKey",
    "GraphDeltaApplyResult",
    "HarnessGraphStore",
    "InMemoryHarnessGraphStore",
    "apply_graph_update_delta",
]
