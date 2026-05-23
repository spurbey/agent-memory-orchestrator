from __future__ import annotations

from typing import Any


class CentralMergeApplyDisabled(RuntimeError):
    pass


def apply_merge_plan(*, plan: dict[str, Any]) -> dict[str, Any]:
    raise CentralMergeApplyDisabled(
        f"central_merge_apply_disabled:{plan.get('plan_id', '')}: "
        "Phase 4 transactional apply is not enabled until dry-run plans and semantic evals are accepted."
    )
