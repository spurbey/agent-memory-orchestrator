"""Compatibility boundaries for staged package refactors.

New code should import from the domain/application/infrastructure/runtime
packages directly. This package records legacy root import surfaces that stay
supported while the repository is migrated in small, verified chunks.
"""

from __future__ import annotations

from .old_imports import (
    COMPATIBILITY_IMPORTS,
    CompatibilityImport,
    canonical_module_for,
    compatibility_imports,
    legacy_modules,
)

__all__ = [
    "COMPATIBILITY_IMPORTS",
    "CompatibilityImport",
    "canonical_module_for",
    "compatibility_imports",
    "legacy_modules",
]
