"""Compatibility wrapper for central merge planning.

New code should import from agent_memory_orchestrator.domain.versioning.central_merge.planner.
"""

from __future__ import annotations

from ...domain.versioning.central_merge.planner import EXACT_ATOM_KINDS
from ...domain.versioning.central_merge.planner import SAFE_APPLY_ATOM_KINDS
from ...domain.versioning.central_merge.planner import build_dry_run_merge_plan

__all__ = ["EXACT_ATOM_KINDS", "SAFE_APPLY_ATOM_KINDS", "build_dry_run_merge_plan"]
