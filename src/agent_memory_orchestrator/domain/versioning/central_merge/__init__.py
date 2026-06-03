"""Central version-merge planning and decision matching domain logic."""

from __future__ import annotations

from .decision import DecisionFrame
from .decision import build_decision_frames
from .decision import build_decision_review_candidates
from .planner import EXACT_ATOM_KINDS
from .planner import SAFE_APPLY_ATOM_KINDS
from .planner import build_dry_run_merge_plan

__all__ = [
    "DecisionFrame",
    "EXACT_ATOM_KINDS",
    "SAFE_APPLY_ATOM_KINDS",
    "build_decision_frames",
    "build_decision_review_candidates",
    "build_dry_run_merge_plan",
]
