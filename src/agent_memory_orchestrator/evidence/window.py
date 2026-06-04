"""Compatibility facade for domain-owned evidence window selection."""

from __future__ import annotations

from ..domain.evidence.windows import MAX_QWEN_CONTENT_CHARS
from ..domain.evidence.windows import MAX_QWEN_RECORDS
from ..domain.evidence.windows import MAX_QWEN_TOTAL_CHARS
from ..domain.evidence.windows import clean_evidence_window

__all__ = ["MAX_QWEN_CONTENT_CHARS", "MAX_QWEN_RECORDS", "MAX_QWEN_TOTAL_CHARS", "clean_evidence_window"]
