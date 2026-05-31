"""Future workflow boundary for blast-radius analysis."""

from __future__ import annotations

from typing import Any, Mapping

from ..services.local_agent_review import LocalAgentReviewService
from ...extensions.contracts.local_agent_skill import LocalAgentSkillResult


class BlastRadiusWorkflow:
    """Route blast-radius requests to a local-agent skill when available."""

    def __init__(self, local_agent_review: LocalAgentReviewService, *, skill_name: str = "blast-radius") -> None:
        self.local_agent_review = local_agent_review
        self.skill_name = skill_name

    def analyze(
        self,
        *,
        target: str,
        repo_id: str = "",
        session_id: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> LocalAgentSkillResult:
        return self.local_agent_review.run(
            skill_name=self.skill_name,
            task=f"Analyze blast radius for {target}",
            repo_id=repo_id,
            session_id=session_id,
            context=context or {},
        )


__all__ = ["BlastRadiusWorkflow"]
