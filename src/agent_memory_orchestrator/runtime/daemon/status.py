from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...core.config import Settings
from .client import DaemonClient
from .client import DaemonUnavailable


@dataclass(slots=True, frozen=True)
class RuntimeDaemonStatus:
    """Runtime adapter exposing daemon health to application workflows."""

    settings: Settings
    timeout_seconds: float = 1.0

    def status(self) -> dict[str, Any]:
        try:
            health = DaemonClient.from_settings(self.settings, timeout_seconds=self.timeout_seconds).health()
        except DaemonUnavailable:
            return {"running": False}
        except Exception:
            return {"running": False}
        return {"running": bool(health.get("ok")), "health": health}


__all__ = ["RuntimeDaemonStatus"]
