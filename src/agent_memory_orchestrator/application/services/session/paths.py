from __future__ import annotations

import os
from pathlib import Path

from .utils import _safe_edge_part


def default_session_graph_path(session_id: str) -> Path:
    safe_session = _safe_edge_part(session_id)
    return Path(os.environ.get("AMO_HOME", str(Path.home() / ".agent-memory-orchestrator"))) / ".graph" / "sessions" / f"{safe_session}.kuzu"


__all__ = ["default_session_graph_path"]
