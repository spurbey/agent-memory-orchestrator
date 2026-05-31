from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from ...core.config import Settings


def optional_payload_path(payload: dict[str, Any], key: str) -> Path | None:
    value = str(payload.get(key) or "").strip()
    if not value:
        return None
    return Path(value).expanduser().resolve()


def settings_with_payload_paths(settings: Settings, payload: dict[str, Any], *, prefer_retrieval: bool = False) -> Settings:
    updates: dict[str, Path] = {}
    db_path = optional_payload_path(payload, "db_path")
    graph_path = optional_payload_path(payload, "graph_path")
    if graph_path is None and prefer_retrieval and settings.retrieval_graph_path is not None:
        graph_path = settings.retrieval_graph_path
    if db_path is not None:
        updates["db_path"] = db_path
    if graph_path is not None:
        updates["graph_path"] = graph_path
    if not updates:
        return settings
    for path in updates.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    return replace(settings, **updates)
