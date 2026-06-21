from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness.runtime.query_planner import plan_query_evidence
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest


def test_context_plan_resolves_symbol_with_file_hint_and_risk_edges() -> None:
    request = HarnessQueryRequest(
        intent="file_context",
        mode="context_for_anchor",
        user_goal="preserve behavior",
        files=("src/auth.py",),
        symbols=("login",),
        questions=("why is this intentional and what would break if changed?",),
    )

    plan = plan_query_evidence("repo:test", request, mode="context_for_anchor")

    assert plan is not None
    assert [(seed.kind, seed.value, seed.path_hint) for seed in plan.seeds] == [
        ("file", "src/auth.py", ""),
        ("symbol", "login", "src/auth.py"),
    ]
    assert {(item.kind, item.direction) for item in plan.expansions} == {
        ("VALIDATED_BY", "outgoing"),
        ("CO_CHANGED_WITH", "outgoing"),
    }


def test_usage_plan_requests_directional_calls_and_imports() -> None:
    request = HarnessQueryRequest(
        intent="context_for_anchor",
        mode="context_for_anchor",
        user_goal="find usage",
        symbols=("src/auth.py::login",),
        questions=("what calls this?",),
    )

    plan = plan_query_evidence("repo:test", request, mode="context_for_anchor")

    assert plan is not None
    assert plan.seeds[0].path_hint == "src/auth.py"
    assert {(item.kind, item.direction) for item in plan.expansions} == {
        ("CALLS", "incoming"),
        ("CALLS", "outgoing"),
        ("IMPORTS", "incoming"),
        ("IMPORTS", "outgoing"),
    }


def test_rank_plan_seeds_only_paths_present_in_tool_output() -> None:
    request = HarnessQueryRequest(
        intent="rank_tool_hits",
        mode="rank_tool_hits",
        user_goal="find auth behavior",
        recent_tool_result={
            "kind": "rg",
            "text": "src/auth.py:10:def login():\ntests/test_auth.py:20:def test_login():\nnot-a-hit",
        },
    )

    plan = plan_query_evidence("repo:test", request, mode="rank_tool_hits")

    assert plan is not None
    assert [seed.value for seed in plan.seeds] == ["src/auth.py", "tests/test_auth.py"]
    assert {(item.kind, item.direction) for item in plan.expansions} == {
        ("DEFINES", "outgoing"),
        ("CONTAINS", "outgoing"),
    }
