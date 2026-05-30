"""Compatibility exports for repo identity resolution."""

from __future__ import annotations

from ...domain.versioning.repo_identity import RepoIdentity
from ...domain.versioning.repo_identity import normalize_remote_url
from ...domain.versioning.repo_identity import resolve_repo_identity

__all__ = ["RepoIdentity", "normalize_remote_url", "resolve_repo_identity"]
