"""Compatibility wrapper for core database helpers.

Use ``agent_memory_orchestrator.core.db`` for new imports.
"""

from .core.db import FTS_SQL, SCHEMA_SQL, connect, init_schema

__all__ = ["FTS_SQL", "SCHEMA_SQL", "connect", "init_schema"]
