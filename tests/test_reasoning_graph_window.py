from __future__ import annotations

from agent_memory_orchestrator.reasoning_graph.extraction_window import build_cleaned_evidence_window
from agent_memory_orchestrator.reasoning_graph.models import TimelineEvent
from agent_memory_orchestrator.reasoning_graph.timeline import TimelineGraph
from agent_memory_orchestrator.reasoning_graph.tool_facts import tool_fact_from_event


def _event(
    event_id: str,
    event_type: str,
    content: str,
    *,
    tool_name: str = "",
    command: str = "",
    files: tuple[str, ...] = (),
) -> TimelineEvent:
    return TimelineEvent(
        id=event_id,
        session_id="session-1",
        event_type=event_type,
        timestamp=event_id,
        source_app="codex",
        evidence_id=f"raw_{event_id}",
        content=content,
        tool_name=tool_name,
        files=files,
        metadata={"tool_input_text": command},
    )


def test_tool_fact_extracts_git_status_changed_files() -> None:
    event = _event(
        "1",
        "post_tool_use",
        " M src/agent_memory_orchestrator/reasoning_graph/chunking.py\n"
        "?? src/agent_memory_orchestrator/reasoning_graph/session_runtime.py\n",
        tool_name="Bash",
        command="git status --short",
    )

    fact = tool_fact_from_event(event)

    assert fact is not None
    assert fact.tool_kind == "git_status"
    assert fact.semantic_payload is True
    assert "src/agent_memory_orchestrator/reasoning_graph/chunking.py" in fact.changed_files
    assert "src/agent_memory_orchestrator/reasoning_graph/session_runtime.py" in fact.changed_files


def test_tool_fact_classifies_git_dash_c_status() -> None:
    event = _event(
        "1",
        "post_tool_use",
        "M src/agent_memory_orchestrator/install_service.py",
        tool_name="Bash",
        command="git -C agent-memory-orchestrator status --short",
    )

    fact = tool_fact_from_event(event)

    assert fact is not None
    assert fact.tool_kind == "git_status"
    assert fact.changed_files == ("src/agent_memory_orchestrator/install_service.py",)


def test_tool_fact_keeps_large_output_raw_only() -> None:
    event = _event(
        "1",
        "post_tool_use",
        "x" * 13_000,
        tool_name="Bash",
        command="Get-Content src/agent_memory_orchestrator/install_service.py",
        files=("src/agent_memory_orchestrator/install_service.py",),
    )

    fact = tool_fact_from_event(event)

    assert fact is not None
    assert fact.raw_only is True
    assert fact.semantic_payload is False
    assert fact.tool_kind == "read_or_search"
    assert "full_output_kept_in_raw_evidence_only" in fact.diagnostics


def test_cleaned_window_keeps_target_write_context_and_test() -> None:
    events = (
        _event("1", "user_prompt_submit", "fix hook launcher install_service.py"),
        _event(
            "2",
            "post_tool_use",
            "Success. Updated the following files:\nM src/agent_memory_orchestrator/install_service.py",
            tool_name="apply_patch",
            command="*** Begin Patch\n*** Update File: src/agent_memory_orchestrator/install_service.py",
        ),
        _event(
            "3",
            "post_tool_use",
            "11 passed in 0.48s",
            tool_name="Bash",
            command="python -m pytest tests/test_install_service.py",
        ),
        _event("4", "post_tool_use", "python processes", tool_name="Bash", command="Get-Process python"),
    )
    timeline = TimelineGraph(session_id="session-1", events=events, edges=())

    window = build_cleaned_evidence_window(
        timeline,
        target_files=("src/agent_memory_orchestrator/install_service.py",),
    )

    assert window.raw_event_count == 4
    assert window.kept_event_count == 3
    assert window.dropped_event_count == 1
    assert any(fact.tool_kind == "write_patch" for fact in window.tool_facts)
    assert any(fact.tool_kind == "test_or_lint" for fact in window.tool_facts)
    assert window.drop_reason_counts["environment_check_unrelated"] == 1


def test_cleaned_window_matches_absolute_path_to_repo_relative_target() -> None:
    events = (
        _event(
            "1",
            "post_tool_use",
            "Success. Updated the following files:\n"
            "M C:\\Users\\sumit\\Downloads\\Dora\\agent-memory-orchestrator\\src\\agent_memory_orchestrator\\install_service.py",
            tool_name="apply_patch",
            command="*** Begin Patch",
        ),
    )
    timeline = TimelineGraph(session_id="session-1", events=events, edges=())

    window = build_cleaned_evidence_window(
        timeline,
        target_files=("src/agent_memory_orchestrator/install_service.py",),
    )

    assert window.kept_event_count == 1
    assert window.window_events[0].keep_reasons[0] == "target_file_changed"
