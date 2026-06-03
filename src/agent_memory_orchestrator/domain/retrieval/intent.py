"""Retrieval intent boundary.

Stage 1 keeps the existing classifier implementation intact while exposing the
planned domain module name for callers that should not depend on legacy layout.
"""

from __future__ import annotations

from .classification import classify_query
from .classification import query_has_code_locator

__all__ = ["classify_query", "query_has_code_locator"]
