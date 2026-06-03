from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class PeerTransportPort(Protocol):
    """Transport boundary for bot-to-bot peer messages."""

    def send(self, peer_id: str, message: Mapping[str, Any]) -> Mapping[str, Any]:
        """Send one message to a peer and return transport metadata."""

    def receive(self, *, limit: int = 50) -> list[Mapping[str, Any]]:
        """Receive pending peer messages without applying memory mutations."""
