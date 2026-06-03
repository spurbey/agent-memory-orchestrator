from __future__ import annotations

from typing import Any

from ....peer.agent import PeerAgentService
from .validation import require_text as _require_text


class PeerToolMixin:
    def peer_memory_ask(
        self,
        *,
        query: str,
        session_id: str = "",
        min_confidence: float = 0.72,
        timeout_seconds: float = 45,
    ) -> dict[str, Any]:
        return self._peer_agent_service().ask(
            query=_require_text(query, "query"),
            session_id=session_id,
            min_confidence=min_confidence,
            timeout_seconds=timeout_seconds,
        )

    def peer_room_status(self, *, room_id: str) -> dict[str, Any]:
        return self._peer_agent_service().status(_require_text(room_id, "room_id"))

    def peer_room_context(self, *, room_id: str) -> dict[str, Any]:
        return self._peer_agent_service().context(_require_text(room_id, "room_id"))

    def peer_room_messages(self, *, room_id: str) -> dict[str, Any]:
        return self._peer_agent_service().messages(_require_text(room_id, "room_id"))

    def _peer_agent_service(self) -> PeerAgentService:
        if self._peer_agent is None:
            self._peer_agent = PeerAgentService(self.settings)
        return self._peer_agent


__all__ = ["PeerToolMixin"]
