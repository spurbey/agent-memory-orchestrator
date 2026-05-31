from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


@dataclass(slots=True, frozen=True)
class LocalAgentSkillRequest:
    task: str
    repo_id: str = ""
    session_id: str = ""
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class LocalAgentSkillResult:
    ok: bool
    summary: str = ""
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    diagnostics: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class LocalAgentSkill(Protocol):
    name: str
    version: str

    def run(self, request: LocalAgentSkillRequest) -> LocalAgentSkillResult:
        """Run a local-only agent skill without mutating AMO stores directly."""
