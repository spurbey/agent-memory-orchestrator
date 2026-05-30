from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class ConnectorTransportPort(Protocol):
    """Transport boundary for external connector IO."""

    def poll(self, *, limit: int = 50) -> list[Mapping[str, Any]]:
        """Poll connector events from an external system."""

    def respond(self, target: Mapping[str, Any], response: Mapping[str, Any]) -> Mapping[str, Any]:
        """Send a connector response and return delivery metadata."""
