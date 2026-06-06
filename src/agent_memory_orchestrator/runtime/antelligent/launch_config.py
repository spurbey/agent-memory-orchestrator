from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from ...core.config import Settings
from ..daemon.antelligent_auth import ensure_antelligent_token
from .paths import paths_for

SCHEMA_VERSION = 1


def build_launch_config(settings: Settings, *, python_executable: str | None = None) -> dict[str, Any]:
    program = str(Path(python_executable or sys.executable).resolve())
    settings.home.mkdir(parents=True, exist_ok=True)
    token = ensure_antelligent_token(settings)
    paths = paths_for(settings)
    return {
        "schema_version": SCHEMA_VERSION,
        "amo_home": str(settings.home),
        "daemon_url": f"http://{settings.mcp_host}:{settings.mcp_port}",
        "daemon_command": {
            "program": program,
            "args": [
                "-m",
                "agent_memory_orchestrator.runtime.daemon.server",
                "--amo-home",
                str(settings.home),
            ],
        },
        "ui_token_path": str(paths.token_path),
        "token_ready": bool(token),
    }


def write_launch_config(settings: Settings, *, python_executable: str | None = None) -> dict[str, Any]:
    payload = build_launch_config(settings, python_executable=python_executable)
    path = paths_for(settings).launch_config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    safe_payload = dict(payload)
    safe_payload.pop("token_ready", None)
    path.write_text(json.dumps(safe_payload, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "path": str(path), "config": safe_payload}


def read_launch_config(settings: Settings) -> dict[str, Any] | None:
    path = paths_for(settings).launch_config_path
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


__all__ = ["build_launch_config", "read_launch_config", "write_launch_config"]
