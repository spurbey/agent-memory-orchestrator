"""Application boundary for future local-agent review skills."""

from __future__ import annotations

from typing import Any, Mapping

from ....extensions.contracts.local_agent_skill import LocalAgentSkill
from ....extensions.contracts.local_agent_skill import LocalAgentSkillRequest
from ....extensions.contracts.local_agent_skill import LocalAgentSkillResult
from ....extensions.registry import ExtensionRegistry


class LocalAgentReviewService:
    """Dispatch local-only review tasks through registered local-agent skills."""

    def __init__(self, registry: ExtensionRegistry | None = None) -> None:
        self.registry = registry or ExtensionRegistry()

    def register(self, skill: LocalAgentSkill) -> None:
        from ....extensions.registry import ExtensionDescriptor

        self.registry.register(
            skill,
            ExtensionDescriptor(
                name=skill.name,
                extension_type="local_agent_skill",
                version=skill.version,
            ),
        )

    def run(
        self,
        *,
        skill_name: str,
        task: str,
        repo_id: str = "",
        session_id: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> LocalAgentSkillResult:
        skill = self.registry.get("local_agent_skill", skill_name)
        if skill is None:
            return LocalAgentSkillResult(
                ok=False,
                summary="Local agent skill is not registered.",
                diagnostics={"missing_skill": skill_name},
            )
        return skill.run(
            LocalAgentSkillRequest(
                task=task,
                repo_id=repo_id,
                session_id=session_id,
                context=context or {},
            )
        )


__all__ = ["LocalAgentReviewService"]
