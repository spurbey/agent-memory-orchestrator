from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_memory_orchestrator.application.ports import CentralMergeStorePort
from agent_memory_orchestrator.infrastructure.sqlite import CentralMergeStore
from agent_memory_orchestrator.infrastructure.sqlite import ProductionSessionJobStore
from agent_memory_orchestrator.infrastructure.kuzu import repo_central_graph_path
from agent_memory_orchestrator.domain.versioning import CONFLICTS_WITH
from agent_memory_orchestrator.domain.versioning import DECISION_REVIEW_RELATIONS
from agent_memory_orchestrator.domain.versioning import DUPLICATE_OF
from agent_memory_orchestrator.domain.versioning import GRAPH_VIEW_AT
from agent_memory_orchestrator.domain.versioning import REFINES
from agent_memory_orchestrator.domain.versioning import STATUS_ACTIVE
from agent_memory_orchestrator.domain.versioning import STATUS_CONTESTED
from agent_memory_orchestrator.domain.versioning import STATUS_REVIEW
from agent_memory_orchestrator.domain.versioning import SUPERSEDES
from agent_memory_orchestrator.domain.versioning import VERSION_OF
from agent_memory_orchestrator.domain.versioning import MergePlan
from agent_memory_orchestrator.domain.versioning import choose_preferred_status
from agent_memory_orchestrator.domain.versioning import graph_commit_id_for_plan
from agent_memory_orchestrator.domain.versioning.graph_views import resolve_graph_view


def test_stage2_graph_commit_id_contract_matches_merge_plan_preview() -> None:
    plan = MergePlan.build(
        job_id="job:stage2",
        session_id="session:stage2",
        repo_id="repo:stage2",
        repo_path=".",
        parent_graph_commit_id="",
        input_graph_hash="input-hash",
        pipeline_version="production",
        graph_schema_version="schema",
    )

    assert plan.graph_commit_preview["graph_commit_id"] == graph_commit_id_for_plan(
        plan_id=plan.plan_id,
        input_graph_hash=plan.input_graph_hash,
    )


def test_stage2_status_priority_contract_preserves_applier_policy() -> None:
    current = choose_preferred_status(None, STATUS_ACTIVE, "new_decision_no_conflict")

    assert current == (STATUS_ACTIVE, "new_decision_no_conflict")
    assert choose_preferred_status(current, STATUS_REVIEW, "lower_priority") == current
    assert choose_preferred_status(current, STATUS_CONTESTED, "conflicts_with_review") == (
        STATUS_CONTESTED,
        "conflicts_with_review",
    )


def test_stage2_merge_relation_contract_names_are_centralized() -> None:
    assert VERSION_OF == "VERSION_OF"
    assert GRAPH_VIEW_AT == "GRAPH_VIEW_AT"
    assert {DUPLICATE_OF, REFINES, SUPERSEDES, CONFLICTS_WITH}.issubset(DECISION_REVIEW_RELATIONS)


def test_stage2_graph_view_boundary_keeps_store_contract() -> None:
    ref = resolve_graph_view(_GraphViewStore(), repo_id="repo:stage2", branch="main", mode="active")

    assert ref.view_id == "view:stage2"
    assert ref.repo_id == "repo:stage2"
    assert ref.branch == "main"
    assert ref.mode == "active"
    assert ref.graph_commit_id == "gcommit:stage2"


def test_stage2_central_graph_path_lives_at_infrastructure_boundary() -> None:
    settings = SimpleNamespace(home=Path("C:/amo-home"))

    path = repo_central_graph_path(settings, "repo:stage2/main")

    assert path == Path("C:/amo-home/.graph/central/repo_stage2_main/central.kuzu")


def test_stage2_central_merge_store_port_uses_sqlite_adapter_alias() -> None:
    assert CentralMergeStore.__name__ == "ProductionSessionJobStore"
    assert CentralMergeStore is ProductionSessionJobStore
    assert CentralMergeStorePort.__name__ == "CentralMergeStorePort"


class _GraphViewStore:
    def ensure_graph_view(self, *, repo_id: str = "", branch: str = "main", mode: str = "active") -> dict[str, object]:
        return {
            "view_id": "view:stage2",
            "repo_id": repo_id,
            "branch": branch,
            "mode": mode,
            "graph_commit_id": "gcommit:stage2",
            "status": "active",
        }
