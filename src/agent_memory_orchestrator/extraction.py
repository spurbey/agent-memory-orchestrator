"""Compatibility wrapper for memory extraction helpers.

Use ``agent_memory_orchestrator.memory.processing.extraction`` for new imports.
"""

from .memory.processing.extraction import (
    MemoryCandidate,
    classify_memory_type,
    confidence_for_signal,
    extract_entities,
    extract_memory_candidates,
    extract_tags,
    make_topic_key,
)

__all__ = [
    "MemoryCandidate",
    "classify_memory_type",
    "confidence_for_signal",
    "extract_entities",
    "extract_memory_candidates",
    "extract_tags",
    "make_topic_key",
]