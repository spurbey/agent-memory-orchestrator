"""Compatibility wrapper for central merge decision matching.

New code should import from agent_memory_orchestrator.domain.versioning.central_merge.decision.
"""

from __future__ import annotations

from ...domain.versioning.central_merge.decision import DecisionFrame
from ...domain.versioning.central_merge.decision import build_decision_frames
from ...domain.versioning.central_merge.decision import build_decision_review_candidates

__all__ = ["DecisionFrame", "build_decision_frames", "build_decision_review_candidates"]
