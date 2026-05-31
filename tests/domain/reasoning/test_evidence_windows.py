from __future__ import annotations

from agent_memory_orchestrator.domain.reasoning.extraction_window import build_cleaned_evidence_window
from agent_memory_orchestrator.domain.reasoning.models import TimelineEvent
from agent_memory_orchestrator.domain.reasoning.timeline import TimelineGraph
from agent_memory_orchestrator.domain.reasoning.tool_facts import tool_fact_from_event
from agent_memory_orchestrator.domain.reasoning.tool_facts import tool_facts_from_events


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
        " M src/agent_memory_orchestrator/domain/reasoning/chunking.py\n"
        "?? src/agent_memory_orchestrator/application/services/session_graph_runtime.py\n",
        tool_name="Bash",
        command="git status --short",
    )

    fact = tool_fact_from_event(event)

    assert fact is not None
    assert fact.tool_kind == "git_status"
    assert fact.semantic_payload is True
    assert "src/agent_memory_orchestrator/domain/reasoning/chunking.py" in fact.changed_files
    assert "src/agent_memory_orchestrator/application/services/session_graph_runtime.py" in fact.changed_files


def test_timeline_event_preserves_dict_tool_response_text() -> None:
    event = TimelineEvent.from_raw_evidence(
        {
            "id": "raw_browser_snapshot",
            "created_at": "2026-05-11T10:07:02+00:00",
            "event_name": "post_tool_use",
            "session_id": "session-1",
            "source_app": "codex",
            "payload": {
                "tool": "mcp__chrome_devtools__take_snapshot",
                "tool_input": {"verbose": False},
                "tool_response": {
                    "content": [
                        {
                            "type": "text",
                            "text": '## Latest page snapshot\nRootWebArea "AMO Control Room"',
                        }
                    ]
                },
            },
        }
    )

    assert event.tool_name == "mcp__chrome_devtools__take_snapshot"
    assert event.metadata["tool_input_text"] == '{"verbose": false}'
    assert 'RootWebArea "AMO Control Room"' in event.content


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


def test_tool_facts_pair_transcript_tool_use_and_result() -> None:
    tool_use = TimelineEvent(
        id="transcript:session-1:tool_use:call_1",
        session_id="session-1",
        event_type="tool_use",
        timestamp="1",
        source_app="codex_transcript",
        content='{"command":"Get-Content src/agent_memory_orchestrator/config.py"}',
        tool_name="shell_command",
        files=("src/agent_memory_orchestrator/config.py",),
        metadata={"call_id": "call_1"},
    )
    tool_result = TimelineEvent(
        id="transcript:session-1:tool_result:call_1",
        session_id="session-1",
        event_type="tool_result",
        timestamp="2",
        source_app="codex_transcript",
        content="class Settings:\n    pass",
        files=("src/agent_memory_orchestrator/config.py",),
        metadata={"call_id": "call_1"},
    )

    facts = tool_facts_from_events((tool_use, tool_result))

    assert len(facts) == 1
    assert facts[0].tool_kind == "read_or_search"
    assert facts[0].command_preview
    assert "class Settings" in facts[0].output_preview
    assert facts[0].metadata["paired_result_event_id"] == tool_result.id


def test_tool_fact_classifies_filesystem_setup() -> None:
    event = _event(
        "1",
        "tool_use",
        'New-Item -ItemType Directory -Force -Path "C:\\repo\\agent-memory-orchestrator\\tests"',
        tool_name="shell_command",
    )

    fact = tool_fact_from_event(event)

    assert fact is not None
    assert fact.tool_kind == "filesystem_write"
    assert fact.semantic_payload is True


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


def test_tool_fact_raw_only_overrides_semantic_payload() -> None:
    event = _event(
        "1",
        "post_tool_use",
        "Success. Updated the following files:\nM src/agent_memory_orchestrator/huge.py\n" + ("x" * 13_000),
        tool_name="apply_patch",
        command="*** Begin Patch\n*** Update File: src/agent_memory_orchestrator/huge.py",
    )

    fact = tool_fact_from_event(event)

    assert fact is not None
    assert fact.tool_kind == "write_patch"
    assert fact.raw_only is True
    assert fact.semantic_payload is False


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


def test_cleaned_window_keeps_setup_context_for_target_cluster() -> None:
    events = (
        _event(
            "1",
            "user_message",
            "# Context from my IDE setup:\n\n"
            "## Active file: temp_repo_arena/README.md\n\n"
            "## Open tabs:\n"
            "- README.md\n\n"
            "## My request for Codex:\n"
            "so, then don't put it under dora/temp_repo_arena.\n\n"
            "make a new repo under dora/",
        ),
        _event(
            "2",
            "tool_use",
            "agent-memory-orchestrator\ntemp_repo_arena",
            tool_name="shell_command",
            command='Get-ChildItem -Name -Path "C:\\repo"',
        ),
        _event(
            "3",
            "tool_use",
            "Exit code: 0",
            tool_name="shell_command",
            command='New-Item -ItemType Directory -Force -Path "C:\\repo\\agent-memory-orchestrator\\src\\agent_memory_orchestrator"',
        ),
        _event(
            "4",
            "tool_use",
            "Success. Updated the following files:\nA C:\\repo\\agent-memory-orchestrator\\src\\agent_memory_orchestrator\\config.py",
            tool_name="apply_patch",
            command="*** Begin Patch\n*** Add File: C:\\repo\\agent-memory-orchestrator\\src\\agent_memory_orchestrator\\config.py",
        ),
    )
    timeline = TimelineGraph(session_id="session-1", events=events, edges=())

    window = build_cleaned_evidence_window(
        timeline,
        target_files=("agent-memory-orchestrator/src/agent_memory_orchestrator/config.py",),
    )

    kinds = [event.tool_fact.tool_kind for event in window.window_events if event.tool_fact]
    assert "read_or_search" in kinds
    assert "filesystem_write" in kinds
    assert "write_patch" in kinds
    assert "make a new repo under dora/" in window.chunk_input_text()
    assert "## Open tabs:" not in window.chunk_input_text()


def test_cleaned_window_skips_turn_aborted_context() -> None:
    events = (
        _event("1", "user_message", "<turn_aborted>\nThe user interrupted the previous turn.\n</turn_aborted>"),
        _event(
            "2",
            "tool_use",
            "Success. Updated the following files:\nA src/agent_memory_orchestrator/config.py",
            tool_name="apply_patch",
            command="*** Begin Patch\n*** Add File: src/agent_memory_orchestrator/config.py",
        ),
    )
    timeline = TimelineGraph(session_id="session-1", events=events, edges=())

    window = build_cleaned_evidence_window(
        timeline,
        target_files=("src/agent_memory_orchestrator/config.py",),
    )

    assert "<turn_aborted>" not in window.chunk_input_text()


def test_cleaned_window_uses_first_target_cluster_only() -> None:
    filler = tuple(
        _event(str(index), "agent_message", f"unrelated later topic {index}")
        for index in range(4, 130)
    )
    events = (
        _event(
            "1",
            "tool_use",
            "Success. Updated the following files:\nA src/agent_memory_orchestrator/config.py",
            tool_name="apply_patch",
            command="*** Begin Patch\n*** Add File: src/agent_memory_orchestrator/config.py",
        ),
        *filler,
        _event(
            "130",
            "tool_use",
            "Success. Updated the following files:\nM src/agent_memory_orchestrator/config.py",
            tool_name="apply_patch",
            command="*** Begin Patch\n*** Update File: src/agent_memory_orchestrator/config.py",
        ),
    )
    timeline = TimelineGraph(session_id="session-1", events=events, edges=())

    window = build_cleaned_evidence_window(
        timeline,
        target_files=("src/agent_memory_orchestrator/config.py",),
    )

    kept_ids = {event.event_id for event in window.window_events}
    assert "1" in kept_ids
    assert "130" not in kept_ids


