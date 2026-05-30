from __future__ import annotations

from ...domain.versioning.models import CENTRAL_MERGE_PLAN_VERSION
from ...domain.versioning.models import MergePlan
from ...domain.versioning.repo_identity import RepoIdentity
from ...domain.versioning.repo_identity import resolve_repo_identity

__all__ = [
    "CENTRAL_MERGE_PLAN_VERSION",
    "MergePlan",
    "RepoIdentity",
    "apply_merge_plan",
    "backfill_central_merge_plan",
    "build_dry_run_merge_plan",
    "export_job_fixture",
    "judge_semantic_case",
    "resolve_repo_identity",
]


def __getattr__(name: str):
    if name == "apply_merge_plan":
        from .applier import apply_merge_plan

        return apply_merge_plan
    if name == "backfill_central_merge_plan":
        from .backfill import backfill_central_merge_plan

        return backfill_central_merge_plan
    if name == "build_dry_run_merge_plan":
        from .planner import build_dry_run_merge_plan

        return build_dry_run_merge_plan
    if name == "export_job_fixture":
        from .fixtures import export_job_fixture

        return export_job_fixture
    if name == "judge_semantic_case":
        from .judge import judge_semantic_case

        return judge_semantic_case
    raise AttributeError(name)
