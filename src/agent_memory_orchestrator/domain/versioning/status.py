"""Status contracts for central KnowledgeVersion and graph lineage nodes."""

from __future__ import annotations

STATUS_ACTIVE = "active"
STATUS_APPLIED = "applied"
STATUS_CONTESTED = "contested"
STATUS_PREVIEW = "preview"
STATUS_REFINED = "refined"
STATUS_REVIEW = "review"
STATUS_REVERTED = "reverted"
STATUS_SUPERSEDED = "superseded"

VERSION_STATUS_PRIORITY = {
    STATUS_CONTESTED: 50,
    STATUS_SUPERSEDED: 40,
    STATUS_REFINED: 30,
    STATUS_ACTIVE: 20,
    STATUS_REVIEW: 10,
}


def choose_preferred_status(
    current: tuple[str, str] | None,
    status: str,
    reason: str,
) -> tuple[str, str]:
    candidate = (str(status or ""), str(reason or ""))
    if current is None:
        return candidate
    if VERSION_STATUS_PRIORITY.get(candidate[0], 0) > VERSION_STATUS_PRIORITY.get(str(current[0]), 0):
        return candidate
    return current


def is_current_status(status: str) -> bool:
    return str(status or "") in {"", STATUS_ACTIVE, STATUS_REVIEW}


__all__ = [
    "STATUS_ACTIVE",
    "STATUS_APPLIED",
    "STATUS_CONTESTED",
    "STATUS_PREVIEW",
    "STATUS_REFINED",
    "STATUS_REVIEW",
    "STATUS_REVERTED",
    "STATUS_SUPERSEDED",
    "VERSION_STATUS_PRIORITY",
    "choose_preferred_status",
    "is_current_status",
]
