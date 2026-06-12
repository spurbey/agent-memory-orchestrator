from __future__ import annotations

import hashlib

DEFAULT_PROJECTION_VERSION = "semantic_harness_projection_v1"


def projection_set_id(
    graph_snapshot_id: str,
    *,
    projection_version: str = DEFAULT_PROJECTION_VERSION,
) -> str:
    stable = "\n".join((projection_version, str(graph_snapshot_id or "")))
    return f"hproj:{_short_hash(stable, size=24)}"


def _short_hash(value: str, *, size: int) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:size]


__all__ = ["DEFAULT_PROJECTION_VERSION", "projection_set_id"]
