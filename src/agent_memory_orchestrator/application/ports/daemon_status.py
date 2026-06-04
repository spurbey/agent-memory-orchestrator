from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class DaemonStatusPort(Protocol):
    """Read-only runtime health boundary for safety-critical workflows."""

    def status(self) -> dict[str, Any]: ...


__all__ = ["DaemonStatusPort"]
