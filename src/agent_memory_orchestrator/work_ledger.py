"""Compatibility wrapper for work-ledger imports.

The canonical import path is now ``agent_memory_orchestrator.versioning``.
"""

from .versioning import WorkLedger, WorkTrace

__all__ = ["WorkLedger", "WorkTrace"]
