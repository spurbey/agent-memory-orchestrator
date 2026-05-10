"""Compatibility wrapper for core privacy helpers.

Use ``agent_memory_orchestrator.core.privacy`` for new imports.
"""

from .core.privacy import redact_secrets

__all__ = ["redact_secrets"]
