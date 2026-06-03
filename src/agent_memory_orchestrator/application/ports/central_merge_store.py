from __future__ import annotations

from typing import Any
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class CentralMergeStorePort(Protocol):
    """Store boundary used by central-version merge orchestration."""

    def ensure_graph_view(self, *, repo_id: str = "", branch: str = "main", mode: str = "active") -> dict[str, Any]:
        ...

    def list_decision_frames(self, *, repo_id: str, exclude_job_id: str = "", status: str = "") -> list[dict[str, Any]]:
        ...

    def upsert_central_merge_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        ...

    def get_central_merge_plan(self, plan_id: str) -> dict[str, Any] | None:
        ...

    def update_central_merge_plan_status(
        self,
        *,
        plan_id: str,
        status: str,
        mode: str | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def acquire_central_merge_lock(
        self,
        *,
        repo_id: str,
        branch: str,
        owner: str,
        expected_parent_graph_commit_id: str,
        lease_seconds: int,
    ) -> bool:
        ...

    def release_central_merge_lock(self, *, repo_id: str, branch: str, owner: str) -> None:
        ...

    def record_applied_graph_commit(
        self,
        *,
        graph_commit_id: str,
        plan_id: str,
        job_id: str,
        repo_id: str,
        branch: str,
        parent_graph_commit_id: str,
        pipeline_version: str,
        graph_schema_version: str,
        algorithm_versions: dict[str, Any],
        added_nodes: list[str],
        added_edges: list[str],
        status_updates: list[dict[str, Any]] | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def update_graph_view_head(
        self,
        *,
        repo_id: str,
        branch: str,
        mode: str,
        graph_commit_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        ...


__all__ = ["CentralMergeStorePort"]
