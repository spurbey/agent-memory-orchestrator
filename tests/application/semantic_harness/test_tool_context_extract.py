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
