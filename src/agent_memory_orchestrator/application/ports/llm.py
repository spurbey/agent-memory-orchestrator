from __future__ import annotations

from typing import Mapping, Protocol, runtime_checkable


@runtime_checkable
class LlmPort(Protocol):
    """LLM boundary for reasoning workflows."""

    def complete(self, prompt: str, *, options: Mapping[str, object] | None = None) -> str:
        """Return one model completion for the supplied prompt."""
