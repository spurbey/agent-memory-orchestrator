"""Compatibility facade for domain-owned evidence trigger rules."""

from __future__ import annotations

from ..domain.evidence.models import TriggerDecision
from ..domain.evidence.triggers import detect_trigger
from ..domain.evidence.triggers import is_session_start
from ..domain.evidence.triggers import record_session_id
from ..domain.evidence.triggers import session_boundary_trigger

__all__ = ["TriggerDecision", "detect_trigger", "is_session_start", "record_session_id", "session_boundary_trigger"]
