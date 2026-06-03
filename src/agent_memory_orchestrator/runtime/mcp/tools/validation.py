from __future__ import annotations

import json
from typing import Any

from .contracts import AGENTS


def require_text(value: str, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def normalize_agent(agent: str) -> str:
    normalized = require_text(agent, "agent").lower()
    if normalized not in AGENTS:
        raise ValueError(f"agent must be one of: {', '.join(sorted(AGENTS))}")
    return normalized


def parse_metadata(metadata_json: str) -> dict[str, Any]:
    if not metadata_json:
        return {}
    try:
        parsed = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"metadata_json must be a JSON object: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("metadata_json must be a JSON object")
    return parsed


def bounded_limit(value: int, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(maximum, parsed))


__all__ = ["bounded_limit", "normalize_agent", "parse_metadata", "require_text"]
