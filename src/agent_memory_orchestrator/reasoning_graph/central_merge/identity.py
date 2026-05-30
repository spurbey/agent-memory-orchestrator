"""Compatibility exports for central version-merge identity helpers."""

from __future__ import annotations

from ...domain.versioning.identity import CanonicalIdentity
from ...domain.versioning.identity import atoms_by_canonical_key
from ...domain.versioning.identity import exact_canonical_key

__all__ = ["CanonicalIdentity", "atoms_by_canonical_key", "exact_canonical_key"]
