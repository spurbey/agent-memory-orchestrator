from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness.tool_context import CapturedToolResult
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import classify_tool_kind
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import extract_tool_result_anchors


def test_extract_rg_files_and_line_refs() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "rg -n login src tests"},
        tool_response="src/auth.py:12:def login():\ntests/test_auth.py:7:assert login()\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "search"
    assert anchors.files == ("src/auth.py", "tests/test_auth.py")
    assert [(ref.file_path, ref.line) for ref in anchors.line_refs] == [("src/auth.py", 12), ("tests/test_auth.py", 7)]


def test_extract_file_read_anchor_and_python_symbols() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "Get-Content src/auth.py"},
        tool_response="class AuthService:\n    pass\n\nasync def refresh_token():\n    return True\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "file_read"
    assert anchors.files == ("src/auth.py",)
    assert anchors.symbols == ("AuthService", "refresh_token")


def test_extract_git_diff_changed_files() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "git diff"},
        tool_response="diff --git a/src/auth.py b/src/auth.py\n--- a/src/auth.py\n+++ b/src/auth.py\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "git_diff"
    assert anchors.files == ("src/auth.py",)


def test_extract_root_config_patch_file() -> None:
    captured = CapturedToolResult(
        tool_name="apply_patch",
        tool_input={"command": "*** Begin Patch\n*** Update File: pyproject.toml\n@@\n"},
        tool_response="Success. Updated the following files:\nM pyproject.toml\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "apply_patch"
    assert anchors.files == ("pyproject.toml",)


def test_extract_pytest_failure_files_and_errors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "python -m pytest"},
        tool_response="FAILED tests/test_auth.py::test_login - AssertionError: bad redirect\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "test_output"
    assert anchors.files == ("tests/test_auth.py",)
    assert anchors.errors == ("FAILED tests/test_auth.py::test_login - AssertionError: bad redirect",)


def test_external_absolute_paths_are_not_repo_anchors_without_cwd() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": r"Get-Content C:\Users\sumit\.codex\skills\SKILL.md"},
        tool_response="# Skill\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()


def test_external_skill_path_does_not_become_docs_anchor_with_cwd() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": r"Get-Content C:\Users\sumit\.codex\skills\.system\openai-docs\SKILL.md"},
        tool_response="# OpenAI docs skill\n",
        cwd=r"C:\Users\sumit\Downloads\Dora",
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()


def test_workspace_parent_prefix_is_stripped_to_repo_relative_path() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "Get-ChildItem"},
        tool_response=(
            r"C:\Users\sumit\Downloads\Dora\agent-memory-orchestrator"
            r"\src\agent_memory_orchestrator\runtime\hook\launcher.py"
        ),
        cwd=r"C:\Users\sumit\Downloads\Dora",
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ("src/agent_memory_orchestrator/runtime/hook/launcher.py",)


def test_appdata_cache_paths_are_not_repo_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": r"node C:\Users\sumit\.codex\skills\fetch.mjs"},
        tool_response=r"C:\Users\sumit\AppData\Local\Temp\openai-docs-cache\codex-manual.md",
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()


def test_prose_mentions_are_not_file_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "Get-Content evidence.jsonl"},
        tool_response="Implemented scraper/retry.py and discussed future fixes.",
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()


def test_codex_manual_links_are_not_repo_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "Get-Content codex-manual.md"},
        tool_response="See (/codex/mcp.md), (/codex/hooks.md), and .codex/config.toml.",
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()


def test_malformed_repo_like_paths_are_not_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "Get-ChildItem"},
        tool_response=(
            "src//agent_memory_orchestrator//domain//semantic_harness//anchor_resolution.py\n"
            "src/agent_memory_orchestrator/domain/semantic_harness/doc_semantics/*.py\n"
        ),
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()


def test_generated_tmp_reports_do_not_feed_overlay_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "Get-Content .tmp/semantic-harness-profile/shadow_replay.json"},
        tool_response='{"files": ["src/auth.py", "docs/SKILL.md"]}',
    )

    anchors = extract_tool_result_anchors(captured)

    assert anchors.files == ()
    assert anchors.errors == ()


def test_git_status_inventory_does_not_feed_overlay_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "git status --short; git branch --show-current"},
        tool_response=" M src/auth.py\n?? tests/test_auth.py\nmain\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "unknown"
    assert anchors.files == ()


def test_git_ls_files_inventory_does_not_feed_overlay_anchors() -> None:
    captured = CapturedToolResult(
        tool_name="shell_command",
        tool_input={"command": "git ls-files | Select-Object -First 10"},
        tool_response="src/auth.py\ntests/test_auth.py\n",
    )

    anchors = extract_tool_result_anchors(captured)

    assert classify_tool_kind(captured) == "unknown"
    assert anchors.files == ()
