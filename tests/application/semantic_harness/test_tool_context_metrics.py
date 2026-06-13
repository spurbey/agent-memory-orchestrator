from __future__ import annotations

from agent_memory_orchestrator.application.services.semantic_harness.tool_context import CapturedToolResult
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ShadowReplayReport
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ToolOverlayDecision
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ToolOverlayEvalRecord
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ToolOverlayLatency
from agent_memory_orchestrator.application.services.semantic_harness.tool_context import ToolResultAnchors


def test_shadow_metrics_gate_token_overhead_on_attached_cards() -> None:
    attached = _record(would_attach=True, token_overhead=300)
    suppressed = _record(would_attach=False, token_overhead=1200)

    metrics = ShadowReplayReport(
        repo_id="repo:test",
        source_path="evidence.jsonl",
        records=(attached, suppressed),
    ).as_dict()["metrics"]

    assert metrics["token_overhead_p95"] == 300
    assert metrics["all_token_overhead_p95"] == 1200


def _record(*, would_attach: bool, token_overhead: int) -> ToolOverlayEvalRecord:
    return ToolOverlayEvalRecord(
        decision=ToolOverlayDecision(
            mode="shadow",
            tool_kind="file_read",
            captured=CapturedToolResult(
                tool_name="shell_command",
                tool_input={"command": "Get-Content src/auth.py"},
                tool_response="def login():\n    return True\n",
            ),
            anchors=ToolResultAnchors(files=("src/auth.py",)),
            harness_request={},
            harness_response={"status": "partial_structural", "cards": []},
            latency=ToolOverlayLatency(),
            would_attach=would_attach,
            token_overhead_estimate=token_overhead,
        )
    )
