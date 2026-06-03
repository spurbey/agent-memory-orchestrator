"""Workflow boundary for peer-assisted context requests."""

from __future__ import annotations

from typing import Any, Protocol


class PeerContextAgent(Protocol):
    def ask(
        self,
        *,
        query: str,
        peer_ids: list[str] | None = None,
        session_id: str = "",
        min_confidence: float | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Ask local retrieval first, then peers when needed."""

    def context(self, room_id: str) -> dict[str, Any]:
        """Read the local peer-agent room context."""


class PeerContextRequestWorkflow:
    """Request context from trusted peer agents through the peer-agent service."""

    def __init__(self, peer_agent: PeerContextAgent) -> None:
        self.peer_agent = peer_agent

    def ask(
        self,
        query: str,
        *,
        peer_ids: list[str] | None = None,
        session_id: str = "",
        min_confidence: float | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.peer_agent.ask(
            query=query,
            peer_ids=peer_ids,
            session_id=session_id,
            min_confidence=min_confidence,
            timeout_seconds=timeout_seconds,
        )

    def context(self, room_id: str) -> dict[str, Any]:
        return self.peer_agent.context(room_id)


__all__ = ["PeerContextAgent", "PeerContextRequestWorkflow"]
