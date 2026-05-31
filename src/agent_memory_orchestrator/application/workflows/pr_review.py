"""Future workflow boundary for local pull-request review."""

from __future__ import annotations

from typing import Any, Mapping

from ..services.local_agent_review import LocalAgentReviewService
from ...extensions.contracts.local_agent_skill import LocalAgentSkillResult


class PullRequestReviewWorkflow:
    """Route PR review requests to a local-agent skill when available."""

    def __init__(self, local_agent_review: LocalAgentReviewService, *, skill_name: str = "pr-review") -> None:
        self.local_agent_review = local_agent_review
        self.skill_name = skill_name

    def review(
        self,
        *,
        target: str = "working tree",
        repo_id: str = "",
        session_id: str = "",
        context: Mapping[str, Any] | None = None,
    ) -> LocalAgentSkillResult:
        return self.local_agent_review.run(
            skill_name=self.skill_name,
            task=f"Review {target}",
            repo_id=repo_id,
            session_id=session_id,
            context=context or {},
        )


__all__ = ["PullRequestReviewWorkflow"]
