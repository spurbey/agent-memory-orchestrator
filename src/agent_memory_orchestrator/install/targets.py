from __future__ import annotations

from .constants import SUPPORTED_TARGETS


def _normalize_target(target: str) -> str:
    selected = target.strip().lower()
    if selected not in SUPPORTED_TARGETS:
        raise ValueError(f"target must be one of: {', '.join(sorted(SUPPORTED_TARGETS))}")
    return selected


def _expand_targets(target: str) -> list[str]:
    return ["codex", "claude"] if target == "all" else [target]
