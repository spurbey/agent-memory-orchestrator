from __future__ import annotations

import re
from pathlib import Path

from ...core.config import Settings


def repo_central_graph_path(settings: Settings, repo_id: str) -> Path:
    """Return the repo-scoped canonical graph path."""

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(repo_id or "unknown")).strip("._-") or "unknown"
    return settings.home / ".graph" / "central" / safe / "central.kuzu"


__all__ = ["repo_central_graph_path"]
