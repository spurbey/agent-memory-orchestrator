from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessRuntimeService
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import CapturedToolResult
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ToolContextPlanner


def test_file_read_replay_generates_grounded_shadow_attach(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    runtime = SemanticHarnessRuntimeService()
    runtime.bootstrap_repo(repo, repo_id="repo:test")
    planner = ToolContextPlanner(runtime=runtime)

    decision = planner.plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "Get-Content src/auth.py"},
            tool_response="def login():\n    return True\n",
            cwd=str(repo),
        ),
    )

    assert decision.mode == "shadow"
    assert decision.tool_kind == "file_read"
    assert decision.would_attach is True
    assert decision.would_replace is False
    assert decision.confidence >= 0.75
    assert decision.anchors.files == ("src/auth.py",)
    assert decision.harness_response["status"] == "partial_structural"


def test_unknown_tool_without_anchors_suppresses() -> None:
    planner = ToolContextPlanner(runtime=SemanticHarnessRuntimeService())

    decision = planner.plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="unknown_tool",
            tool_input={},
            tool_response="ok",
        ),
    )

    assert decision.would_attach is False
    assert "no_extracted_anchors" in decision.suppression_reasons
    assert "status:unavailable" in decision.suppression_reasons


def test_missing_graph_returns_unavailable_fast_and_suppresses() -> None:
    planner = ToolContextPlanner(runtime=SemanticHarnessRuntimeService())

    decision = planner.plan(
        repo_id="repo:missing",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "Get-Content src/auth.py"},
            tool_response="def login():\n    return True\n",
        ),
    )

    assert decision.harness_response["status"] == "unavailable"
    assert decision.would_attach is False
    assert "status:unavailable" in decision.suppression_reasons
