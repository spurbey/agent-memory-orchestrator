from __future__ import annotations

from collections.abc import Callable

from agent_memory_orchestrator.domain.semantic_harness import HarnessNextAction
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryResponse
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.query_modes import SUPPORTED_QUERY_MODES
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_context_for_anchor

LegacyQuery = Callable[[], HarnessQueryResponse]


def answer_runtime_query(
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    *,
    legacy_query: LegacyQuery,
) -> HarnessQueryResponse:
    mode = _explicit_mode(request)
    if not mode:
        return legacy_query()
    if mode == "context_for_anchor":
        return _answer_context_for_anchor(graph, request, mode=mode)
    legacy = legacy_query()
    return HarnessQueryResponse(
        status=legacy.status,
        intent_requested=legacy.intent_requested,
        intent_used=legacy.intent_used,
        intent_correction=legacy.intent_correction,
        cards=legacy.cards,
        next_actions=legacy.next_actions,
        trace=legacy.trace,
        warnings=tuple(dict.fromkeys((*legacy.warnings, f"unsupported_mode:{mode}"))),
        mode_result=legacy.mode_result,
    )


def _answer_context_for_anchor(
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    *,
    mode: str,
) -> HarnessQueryResponse:
    result = answer_context_for_anchor(
        graph,
        goal=request.user_goal,
        files=request.files,
        symbols=request.symbols,
        questions=request.questions,
        max_results=request.max_cards,
    )
    return HarnessQueryResponse(
        status=result.status,
        intent_requested=request.mode or request.intent,
        intent_used=mode,
        intent_correction=None,
        cards=(),
        next_actions=_next_actions_for_context(result.recommended_next_mode),
        trace=_trace_for_context(result.as_dict()),
        warnings=result.warnings,
        mode_result=result.as_dict(),
    )


def _explicit_mode(request: HarnessQueryRequest) -> str:
    requested_mode = str(request.mode or "").strip()
    if requested_mode:
        return requested_mode
    intent = str(request.intent or "").strip()
    if intent in SUPPORTED_QUERY_MODES:
        return intent
    return ""


def _next_actions_for_context(recommended_mode: str | None) -> tuple[HarnessNextAction, ...]:
    if not recommended_mode:
        return ()
    return (
        HarnessNextAction(
            action_type="call_harness",
            target=recommended_mode,
            reason="Question requires a deeper harness mode than context_for_anchor.",
            priority="recommended",
        ),
    )


def _trace_for_context(mode_result: dict[str, object]) -> dict[str, object]:
    nodes: list[str] = []
    for answer in mode_result.get("answers", []):
        if not isinstance(answer, dict):
            continue
        for evidence in answer.get("evidence", []):
            if isinstance(evidence, dict) and (node_id := str(evidence.get("node_id") or "")):
                nodes.append(node_id)
    for link in mode_result.get("action_relevant_links", []):
        if isinstance(link, dict) and (node_id := str(link.get("target_node_id") or "")):
            nodes.append(node_id)
    return {
        "nodes": list(dict.fromkeys(nodes)),
        "edges": [],
        "versions": [],
        "occurrences": [],
        "mode": mode_result,
    }


__all__ = ["answer_runtime_query"]
