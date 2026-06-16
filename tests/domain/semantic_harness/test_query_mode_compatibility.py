from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness.query_modes import resolve_query_mode


def test_explicit_supported_mode_wins_over_intent() -> None:
    result = resolve_query_mode(mode="rank_tool_hits", intent="file_context")

    assert result.mode_requested == "rank_tool_hits"
    assert result.mode_used == "rank_tool_hits"
    assert result.source == "mode"
    assert result.warnings == ()


def test_unknown_mode_falls_back_to_context_with_warning() -> None:
    result = resolve_query_mode(mode="rewrite_everything")

    assert result.mode_used == "context_for_anchor"
    assert result.warnings == ("unsupported_mode:rewrite_everything",)


def test_tool_overlay_maps_to_rank_tool_hits_for_search_results() -> None:
    result = resolve_query_mode(
        intent="tool_overlay",
        recent_tool_result={"kind": "rg", "text": "src/app.py:1:def run():"},
    )

    assert result.mode_used == "rank_tool_hits"


def test_tool_overlay_without_search_result_stays_contextual() -> None:
    result = resolve_query_mode(intent="tool_overlay", recent_tool_result={"kind": "file_read"})

    assert result.mode_used == "context_for_anchor"


def test_edit_and_test_intents_map_by_context() -> None:
    assert resolve_query_mode(intent="edit_plan").mode_used == "context_for_anchor"
    assert resolve_query_mode(intent="edit_plan", planned_edits=({"file": "src/app.py"},)).mode_used == "pre_edit_review"
    assert resolve_query_mode(intent="test_plan").mode_used == "pre_edit_review"


def test_unknown_intent_preserves_safe_legacy_warning() -> None:
    result = resolve_query_mode(intent="custom_rewrite")

    assert result.mode_used == "context_for_anchor"
    assert result.warnings == ("unsupported_intent:custom_rewrite",)
