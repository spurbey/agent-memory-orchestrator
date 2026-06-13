from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness import SemanticHarnessRuntimeService
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import CapturedToolResult
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ToolContextPlanner


def _runtime_for_tool_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    (repo / "tests" / "test_auth.py").write_text("def test_login():\n    assert True\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    runtime = SemanticHarnessRuntimeService()
    runtime.bootstrap_repo(repo, repo_id="repo:test")
    return repo, runtime


def test_file_read_same_file_only_card_suppresses_as_redundant(tmp_path) -> None:
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
    assert decision.would_attach is False
    assert decision.would_replace is False
    assert decision.anchors.files == ("src/auth.py",)
    assert decision.harness_response["status"] == "partial_structural"
    assert "redundant_file_read_card" in decision.suppression_reasons
    assert "low_card_confidence" in decision.suppression_reasons


def test_test_output_failure_generates_test_target_card(tmp_path) -> None:
    repo, runtime = _runtime_for_tool_repo(tmp_path)

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "python -m pytest tests/test_auth.py -q"},
            tool_response="FAILED tests/test_auth.py::test_login - AssertionError: bad redirect\n",
            cwd=str(repo),
        ),
    )

    assert decision.tool_kind == "test_output"
    assert decision.would_attach is True
    assert decision.harness_response["cards"][0]["type"] == "test_target"
    assert decision.harness_response["cards"][0]["confidence"] >= 0.75


def test_git_diff_generates_diff_impact_card(tmp_path) -> None:
    repo, runtime = _runtime_for_tool_repo(tmp_path)

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "git diff -- src/auth.py"},
            tool_response="diff --git a/src/auth.py b/src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n",
            cwd=str(repo),
        ),
    )

    assert decision.tool_kind == "git_diff"
    assert decision.would_attach is True
    assert decision.harness_response["cards"][0]["type"] == "risk"
    assert decision.harness_response["cards"][0]["title"] == "Review diff impact for src/auth.py"


def test_broad_search_anchor_only_cards_suppress(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    lines: list[str] = []
    for index in range(9):
        path = repo / "src" / f"mod_{index}.py"
        path.write_text(f"def target_{index}():\n    return True\n", encoding="utf-8")
        lines.append(f"src/mod_{index}.py:1:def target_{index}():")
    runtime = SemanticHarnessRuntimeService()
    runtime.bootstrap_repo(repo, repo_id="repo:test")

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "rg -n target src -S"},
            tool_response="\n".join(lines),
            cwd=str(repo),
        ),
    )

    assert decision.tool_kind == "search"
    assert decision.would_attach is False
    assert "broad_search_anchor_only_card" in decision.suppression_reasons


def test_apply_patch_generates_patch_impact_card_for_root_config(tmp_path) -> None:
    repo, runtime = _runtime_for_tool_repo(tmp_path)

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="apply_patch",
            tool_input={"command": "*** Begin Patch\n*** Update File: pyproject.toml\n@@\n"},
            tool_response="Success. Updated the following files:\nM pyproject.toml\n",
            cwd=str(repo),
        ),
    )

    assert decision.tool_kind == "apply_patch"
    assert decision.would_attach is True
    assert decision.harness_response["cards"][0]["type"] == "risk"
    assert decision.harness_response["cards"][0]["title"] == "Verify patch impact for pyproject.toml"


def test_apply_patch_honors_seen_tool_cards(tmp_path) -> None:
    repo, runtime = _runtime_for_tool_repo(tmp_path)
    planner = ToolContextPlanner(runtime=runtime)
    captured = CapturedToolResult(
        tool_name="apply_patch",
        tool_input={"command": "*** Begin Patch\n*** Update File: src/auth.py\n@@\n"},
        tool_response="Success. Updated the following files:\nM src/auth.py\n",
        cwd=str(repo),
    )

    first = planner.plan(repo_id="repo:test", captured=captured)
    seen = tuple(card["card_id"] for card in first.harness_response["cards"])
    second = planner.plan(repo_id="repo:test", captured=captured, already_seen_card_ids=seen)

    assert first.would_attach is True
    assert second.would_attach is False
    assert "duplicate_card" in second.suppression_reasons
    assert "no_cards" in second.suppression_reasons


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
    assert decision.latency.harness_query_ms == 0


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


def test_unresolved_tool_anchors_do_not_trigger_fallback_cards(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    runtime = SemanticHarnessRuntimeService()
    runtime.bootstrap_repo(repo, repo_id="repo:test")

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "Get-Content src/missing.py"},
            tool_response="No such file or directory: src/missing.py",
            cwd=str(repo),
        ),
    )

    assert decision.harness_response["status"] == "unavailable"
    assert decision.harness_response["cards"] == []
    assert decision.harness_response["warnings"] == ["ungrounded_tool_anchors:file:src/missing.py"]
    assert decision.would_attach is False


def test_mixed_tool_anchors_query_only_grounded_subset(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "auth.py").write_text("def login():\n    return True\n", encoding="utf-8")
    runtime = SemanticHarnessRuntimeService()
    runtime.bootstrap_repo(repo, repo_id="repo:test")

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "Get-Content src/auth.py src/missing.py"},
            tool_response="def login():\n    return True\nNo such file: src/missing.py",
            cwd=str(repo),
        ),
    )

    assert decision.harness_response["status"] == "partial_structural"
    assert decision.harness_response["cards"]
    assert decision.harness_response["warnings"] == [
        "structural_only:no_work_history_or_semantic_reasoning_attached",
        "redundant_file_read_card",
        "ungrounded_tool_anchors_filtered:file:src/missing.py",
    ]
    assert decision.would_attach is False
    assert "low_card_confidence" in decision.suppression_reasons


def test_file_read_prefers_file_anchor_over_unresolved_content_symbols(tmp_path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "hook.py").write_text("def captured_symbol():\n    return True\n", encoding="utf-8")
    runtime = SemanticHarnessRuntimeService()
    runtime.bootstrap_repo(repo, repo_id="repo:test")

    decision = ToolContextPlanner(runtime=runtime).plan(
        repo_id="repo:test",
        captured=CapturedToolResult(
            tool_name="shell_command",
            tool_input={"command": "Get-Content src/hook.py | Select-Object -First 5"},
            tool_response="def captured_symbol():\n    pass\n\ndef symbol_not_in_graph():\n    pass\n",
            cwd=str(repo),
        ),
    )

    assert decision.harness_response["status"] == "partial_structural"
    assert decision.would_attach is False
    assert "redundant_file_read_card" in decision.suppression_reasons
    assert decision.token_overhead_estimate <= 900
