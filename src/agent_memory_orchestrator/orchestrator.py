"""Compatibility wrapper for the orchestration service.

The canonical import path is now ``agent_memory_orchestrator.orchestration``.
"""

from .orchestration.service import OrchestratorService

__all__ = ["OrchestratorService"]
