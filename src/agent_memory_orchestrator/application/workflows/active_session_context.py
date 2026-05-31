"""Workflow boundary for active-session context assembly."""

from __future__ import annotations

from typing import Any, Protocol


class ContextPackService(Protocol):
    def build_context_pack(
        self,
        query: str,
        session_id: str | None = None,
        budget_tokens: int | None = None,
        limit: int = 12,
        *,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        """Build the existing AMO context pack payload."""


class ActiveSessionContextWorkflow:
    """Assemble local context for an active agent session."""

    def __init__(self, memory: ContextPackService) -> None:
        self.memory = memory

    def build(
        self,
        query: str,
        *,
        session_id: str | None = None,
        budget_tokens: int | None = None,
        limit: int = 12,
        include_historical: bool = False,
    ) -> dict[str, Any]:
        return self.memory.build_context_pack(
            query,
            session_id=session_id,
            budget_tokens=budget_tokens,
            limit=limit,
            include_historical=include_historical,
        )


__all__ = ["ActiveSessionContextWorkflow", "ContextPackService"]
