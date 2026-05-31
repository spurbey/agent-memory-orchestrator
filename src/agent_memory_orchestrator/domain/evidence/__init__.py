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

__all__ = [
    "DrainSessionState",
    "EvidenceDrain",
    "RawEvidenceRef",
    "RawEvidenceStore",
    "TriggerDecision",
    "clean_evidence_window",
    "detect_trigger",
    "is_session_start",
    "record_session_id",
    "session_boundary_trigger",
]
