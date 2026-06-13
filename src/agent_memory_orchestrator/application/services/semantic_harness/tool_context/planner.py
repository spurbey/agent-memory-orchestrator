from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

from agent_memory_orchestrator.application.services.semantic_harness.runtime import SemanticHarnessRuntimeService
from agent_memory_orchestrator.domain.semantic_harness import HarnessCard
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryResponse

from .extract import classify_tool_kind
from .extract import extract_tool_result_anchors
from .models import CapturedToolResult
from .models import ToolOverlayDecision
from .models import ToolOverlayLatency
from .models import ToolResultAnchors


ATTACHABLE_STATUSES = {"ready", "partial_structural", "partial_historical"}
MIN_ATTACH_CONFIDENCE = 0.75

_INTENT_BY_TOOL_KIND = {
    "search": "tool_overlay",
    "file_read": "file_context",
    "git_diff": "impact_check",
    "test_output": "test_plan",
    "apply_patch": "impact_check",
    "mcp_result": "tool_overlay",
    "unknown": "tool_overlay",
}


@dataclass(slots=True, frozen=True)
class ToolContextPlannerOptions:
    max_cards: int = 5
    max_tokens: int = 900
    max_latency_ms: int = 500
    detail: str = "strict"
    min_attach_confidence: float = MIN_ATTACH_CONFIDENCE


class ToolContextPlanner:
    """Turns captured tool results into shadow harness attach/suppress decisions."""

    def __init__(
        self,
        *,
        runtime: SemanticHarnessRuntimeService,
        options: ToolContextPlannerOptions | None = None,
    ) -> None:
        self._runtime = runtime
        self._options = options or ToolContextPlannerOptions()

    def plan(
        self,
        *,
        repo_id: str,
        captured: CapturedToolResult,
        already_seen_card_ids: tuple[str, ...] = (),
    ) -> ToolOverlayDecision:
        parse_start = time.perf_counter()
        tool_kind = classify_tool_kind(captured)
        anchors = extract_tool_result_anchors(captured)
        request = self._request_for(
            tool_kind=tool_kind,
            captured=captured,
            anchors=anchors,
            already_seen_card_ids=already_seen_card_ids,
        )
        parse_ms = _elapsed_ms(parse_start)

        query_start = time.perf_counter()
        response = self._runtime.query(repo_id, request)
        query_ms = _elapsed_ms(query_start)

        decision_start = time.perf_counter()
        token_overhead = _estimate_token_overhead(response.cards)
        confidence = _max_card_confidence(response.cards)
        suppression_reasons = _suppression_reasons(
            response=response,
            anchors=anchors,
            confidence=confidence,
            token_overhead=token_overhead,
            elapsed_ms=parse_ms + query_ms,
            options=self._options,
        )
        decision_ms = _elapsed_ms(decision_start)
        total_latency = ToolOverlayLatency(
            parse_ms=parse_ms,
            harness_query_ms=query_ms,
            decision_ms=decision_ms,
        )
        if total_latency.total_ms > self._options.max_latency_ms and "latency_exceeded" not in suppression_reasons:
            suppression_reasons = (*suppression_reasons, "latency_exceeded")

        return ToolOverlayDecision(
            mode="shadow",
            tool_kind=tool_kind,
            captured=captured,
            anchors=anchors,
            harness_request=_request_as_dict(request),
            harness_response=response.as_dict(),
            latency=total_latency,
            would_attach=not suppression_reasons,
            would_replace=False,
            suppression_reasons=suppression_reasons,
            confidence=confidence,
            token_overhead_estimate=token_overhead,
        )

    def _request_for(
        self,
        *,
        tool_kind: str,
        captured: CapturedToolResult,
        anchors: ToolResultAnchors,
        already_seen_card_ids: tuple[str, ...],
    ) -> HarnessQueryRequest:
        intent = _INTENT_BY_TOOL_KIND.get(tool_kind, "tool_overlay")
        return HarnessQueryRequest(
            intent=intent,
            user_goal=f"Interpret {tool_kind} result for coding-agent harness overlay.",
            files=anchors.files,
            symbols=anchors.symbols,
            errors=anchors.errors,
            recent_tool_result={
                "tool_name": captured.tool_name,
                "tool_kind": tool_kind,
                "command": captured.command,
                "raw_output_hash": captured.raw_output_hash,
                "response_excerpt": captured.output_excerpt(1200),
            },
            max_cards=self._options.max_cards,
            max_tokens=self._options.max_tokens,
            detail=self._options.detail,
            session_id=captured.session_id,
            already_seen_card_ids=already_seen_card_ids,
        )


def _suppression_reasons(
    *,
    response: HarnessQueryResponse,
    anchors: ToolResultAnchors,
    confidence: float,
    token_overhead: int,
    elapsed_ms: int,
    options: ToolContextPlannerOptions,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if not anchors.has_any:
        reasons.append("no_extracted_anchors")
    if response.status not in ATTACHABLE_STATUSES:
        reasons.append(f"status:{response.status}")
    if not response.cards:
        reasons.append("no_cards")
    if confidence < options.min_attach_confidence:
        reasons.append("low_card_confidence")
    if not _has_graph_grounding(response.cards):
        reasons.append("missing_graph_grounding")
    if _has_only_vector_evidence(response.cards):
        reasons.append("vector_only_evidence")
    if token_overhead > options.max_tokens:
        reasons.append("token_budget_exceeded")
    if elapsed_ms > options.max_latency_ms:
        reasons.append("latency_exceeded")
    return tuple(dict.fromkeys(reasons))


def _has_graph_grounding(cards: tuple[HarnessCard, ...]) -> bool:
    for card in cards:
        for evidence in card.evidence:
            if evidence.get("node_id") and evidence.get("kind") != "ProjectionDocument":
                return True
            if evidence.get("source_id") and evidence.get("target_id") and evidence.get("kind") != "ProjectionDocument":
                return True
    return False


def _has_only_vector_evidence(cards: tuple[HarnessCard, ...]) -> bool:
    has_any = False
    for card in cards:
        for evidence in card.evidence:
            has_any = True
            if evidence.get("retrieval_source") != "vector":
                return False
    return has_any


def _max_card_confidence(cards: tuple[HarnessCard, ...]) -> float:
    return max((float(card.confidence) for card in cards), default=0.0)


def _estimate_token_overhead(cards: tuple[HarnessCard, ...]) -> int:
    text = json.dumps([card.as_dict() for card in cards], sort_keys=True, ensure_ascii=False)
    return max(0, int(len(text) / 4))


def _request_as_dict(request: HarnessQueryRequest) -> dict[str, Any]:
    return {
        "intent": request.intent,
        "user_goal": request.user_goal,
        "anchors": {
            "files": list(request.files),
            "symbols": list(request.symbols),
            "commits": list(request.commits),
            "errors": list(request.errors),
            "recent_tool_result": request.recent_tool_result,
        },
        "budget": {
            "max_cards": request.max_cards,
            "max_tokens": request.max_tokens,
            "detail": request.detail,
        },
        "session_state": {
            "already_seen_node_ids": list(request.already_seen_node_ids),
            "already_seen_relation_ids": list(request.already_seen_relation_ids),
            "already_seen_card_ids": list(request.already_seen_card_ids),
        },
    }


def _elapsed_ms(start: float) -> int:
    return int(round((time.perf_counter() - start) * 1000))


__all__ = ["ToolContextPlanner", "ToolContextPlannerOptions"]
