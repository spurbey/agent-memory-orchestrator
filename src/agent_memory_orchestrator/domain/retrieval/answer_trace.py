"""Retrieval answer-trace boundary.

The implementation still lives in the existing graph package for this tranche.
This module provides the Stage 1 domain import path without changing behavior.
"""

from __future__ import annotations

from ...graph.answer_trace import build_answer_trace
from ...graph.answer_trace import build_central_answer_trace
from ...graph.answer_trace import format_answer_trace

__all__ = ["build_answer_trace", "build_central_answer_trace", "format_answer_trace"]
