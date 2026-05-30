"""Compatibility exports for central version-merge domain contracts."""

from __future__ import annotations

from ...domain.versioning.models import CANONICAL_KEY_VERSION
from ...domain.versioning.models import CENTRAL_MERGE_PLAN_VERSION
from ...domain.versioning.models import KnowledgeAtomPreview
from ...domain.versioning.models import KnowledgeVersionPreview
from ...domain.versioning.models import MergePlan
from ...domain.versioning.models import ReviewCandidate
from ...domain.versioning.models import merge_plan_id_for
from ...domain.versioning.models import stable_hash
from ...domain.versioning.models import utc_now

__all__ = [
    "CANONICAL_KEY_VERSION",
    "CENTRAL_MERGE_PLAN_VERSION",
    "KnowledgeAtomPreview",
    "KnowledgeVersionPreview",
    "MergePlan",
    "ReviewCandidate",
    "merge_plan_id_for",
    "stable_hash",
    "utc_now",
]
