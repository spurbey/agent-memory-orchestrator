"""Compatibility wrapper for central merge apply operations.

New code should import from agent_memory_orchestrator.application.services.central_merge.apply.
"""

from __future__ import annotations

from ...application.services.central_merge.apply import APPLIER_VERSION
from ...application.services.central_merge.apply import APPLY_ATOM_KINDS
from ...application.services.central_merge.apply import EXACT_APPLY_ATOM_KINDS
from ...application.services.central_merge.apply import REVIEW_APPLY_ATOM_KINDS
from ...application.services.central_merge.apply import CentralMergeApplyError
from ...application.services.central_merge.apply import apply_merge_plan
from ...application.services.central_merge.apply import repo_central_graph_path

__all__ = [
    "APPLIER_VERSION",
    "APPLY_ATOM_KINDS",
    "EXACT_APPLY_ATOM_KINDS",
    "REVIEW_APPLY_ATOM_KINDS",
    "CentralMergeApplyError",
    "apply_merge_plan",
    "repo_central_graph_path",
]
