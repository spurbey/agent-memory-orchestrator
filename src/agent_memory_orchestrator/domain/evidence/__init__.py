"""Evidence domain contracts.

The append-only evidence implementation remains in ``agent_memory_orchestrator.evidence``.
This package is the planned domain boundary for callers that should not depend
on implementation layout.
"""

from __future__ import annotations

from .boundaries import EvidenceDrain
from .boundaries import RawEvidenceStore
from .boundaries import clean_evidence_window
from .models import DrainSessionState
from .models import RawEvidenceRef
from .models import TriggerDecision
from .triggers import detect_trigger
from .triggers import is_session_start
from .triggers import record_session_id
from .triggers import session_boundary_trigger
from .views import DEFAULT_CODE_WRITE_SAMPLE_LIMIT
from .views import REASONING_EVIDENCE_VIEW_SCHEMA_VERSION
from .views import REASONING_EVIDENCE_VIEW_STAGE
from .views import ReasoningEvidenceViewBuild
from .views import build_reasoning_evidence_view
from .views import git_commit_truth
from .views import reasoning_evidence_view_contains_raw_internal_ids
from .views import write_reasoning_evidence_view_artifacts

__all__ = [
    "DEFAULT_CODE_WRITE_SAMPLE_LIMIT",
    "DrainSessionState",
    "EvidenceDrain",
    "RawEvidenceRef",
    "RawEvidenceStore",
    "REASONING_EVIDENCE_VIEW_SCHEMA_VERSION",
    "REASONING_EVIDENCE_VIEW_STAGE",
    "ReasoningEvidenceViewBuild",
    "TriggerDecision",
    "build_reasoning_evidence_view",
    "clean_evidence_window",
    "detect_trigger",
    "git_commit_truth",
    "is_session_start",
    "record_session_id",
    "reasoning_evidence_view_contains_raw_internal_ids",
    "session_boundary_trigger",
    "write_reasoning_evidence_view_artifacts",
]
