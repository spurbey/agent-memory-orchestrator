from __future__ import annotations

from agent_memory_orchestrator.extensions.contracts.local_agent_skill import LocalAgentSkillRequest
from agent_memory_orchestrator.extensions.contracts.local_agent_skill import LocalAgentSkillResult


def test_local_agent_review_service_reports_missing_skill() -> None:
    from agent_memory_orchestrator.application.services import LocalAgentReviewService

    result = LocalAgentReviewService().run(skill_name="missing", task="review")

    assert result.ok is False
    assert result.diagnostics["missing_skill"] == "missing"


def test_local_agent_review_service_dispatches_registered_skill() -> None:
    from agent_memory_orchestrator.application.services import LocalAgentReviewService

    service = LocalAgentReviewService()
    service.register(_Skill("pr-review"))

    result = service.run(skill_name="pr-review", task="Review working tree", repo_id="repo:1", session_id="s1")

    assert result.ok is True
    assert result.summary == "pr-review:Review working tree:repo:1:s1"


def test_pr_and_blast_radius_workflows_delegate_to_local_agent_service() -> None:
    from agent_memory_orchestrator.application.services import LocalAgentReviewService
    from agent_memory_orchestrator.application.workflows import BlastRadiusWorkflow
    from agent_memory_orchestrator.application.workflows import PullRequestReviewWorkflow

    service = LocalAgentReviewService()
    service.register(_Skill("pr-review"))
    service.register(_Skill("blast-radius"))

    pr = PullRequestReviewWorkflow(service).review(target="PR #1", repo_id="repo:1")
    blast = BlastRadiusWorkflow(service).analyze(target="src/app.py", repo_id="repo:1")

    assert pr.summary == "pr-review:Review PR #1:repo:1:"
    assert blast.summary == "blast-radius:Analyze blast radius for src/app.py:repo:1:"


class _Skill:
    version = "test"

    def __init__(self, name: str) -> None:
        self.name = name

    def run(self, request: LocalAgentSkillRequest) -> LocalAgentSkillResult:
        return LocalAgentSkillResult(
            ok=True,
            summary=f"{self.name}:{request.task}:{request.repo_id}:{request.session_id}",
        )
