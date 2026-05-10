"""Compatibility wrapper for memory chunking helpers.

Use ``agent_memory_orchestrator.memory.processing.chunker`` for new imports.
"""

from .memory.processing.chunker import (
    ChunkCandidate,
    chunk_text,
    classify_content_type,
)

__all__ = ["ChunkCandidate", "chunk_text", "classify_content_type"]
