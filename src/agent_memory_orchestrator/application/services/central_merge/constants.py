from __future__ import annotations


EXACT_APPLY_ATOM_KINDS = frozenset({"commit", "file"})
REVIEW_APPLY_ATOM_KINDS = frozenset({"decision", "problem"})
APPLY_ATOM_KINDS = EXACT_APPLY_ATOM_KINDS | REVIEW_APPLY_ATOM_KINDS
APPLIER_VERSION = "central-commit-file-decision-status-applier-v1"

__all__ = [
    "APPLIER_VERSION",
    "APPLY_ATOM_KINDS",
    "EXACT_APPLY_ATOM_KINDS",
    "REVIEW_APPLY_ATOM_KINDS",
]
