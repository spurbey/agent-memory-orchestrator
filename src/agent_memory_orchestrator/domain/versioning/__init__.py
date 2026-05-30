"""Graph and code versioning domain contracts."""

from __future__ import annotations

from .identity import CanonicalIdentity
from .identity import atoms_by_canonical_key
from .identity import exact_canonical_key
from .models import CANONICAL_KEY_VERSION
from .models import CENTRAL_MERGE_PLAN_VERSION
from .models import KnowledgeAtomPreview
from .models import KnowledgeVersionPreview
from .models import MergePlan
from .models import ReviewCandidate
from .models import merge_plan_id_for
from .models import stable_hash
from .models import utc_now
from .repo_identity import RepoIdentity
from .repo_identity import normalize_remote_url
from .repo_identity import resolve_repo_identity
from .views import GraphViewRef
from .views import GraphViewStore
from .views import resolve_graph_view

__all__ = [
    "CANONICAL_KEY_VERSION",
    "CENTRAL_MERGE_PLAN_VERSION",
    "CanonicalIdentity",
    "GraphViewRef",
    "GraphViewStore",
    "KnowledgeAtomPreview",
    "KnowledgeVersionPreview",
    "MergePlan",
    "RepoIdentity",
    "ReviewCandidate",
    "atoms_by_canonical_key",
    "exact_canonical_key",
    "merge_plan_id_for",
    "normalize_remote_url",
    "resolve_graph_view",
    "resolve_repo_identity",
    "stable_hash",
    "utc_now",
]
