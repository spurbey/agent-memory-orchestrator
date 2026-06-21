from __future__ import annotations

from collections.abc import Callable

from agent_memory_orchestrator.domain.semantic_harness import HarnessNextAction
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryResponse
from agent_memory_orchestrator.domain.semantic_harness import HarnessProjectionDocument
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness.query_modes import SUPPORTED_QUERY_MODES
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_context_for_anchor
from agent_memory_orchestrator.domain.semantic_harness.query_modes import answer_rank_tool_hits
from agent_memory_orchestrator.domain.semantic_harness.query_modes import resolve_query_mode

LegacyQuery = Callable[[], HarnessQueryResponse]
ProjectionDocumentProvider = Callable[[], tuple[HarnessProjectionDocument, ...]]


def answer_runtime_query(
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    *,
    legacy_query: LegacyQuery,
    projection_document_provider: ProjectionDocumentProvider | None = None,
) -> HarnessQueryResponse:
    mode = explicit_query_mode(request)
    if not mode:
        return legacy_query()
    if mode == "context_for_anchor":
        return _answer_context_for_anchor(graph, request, mode=mode)
    if mode == "rank_tool_hits":
        return _answer_rank_tool_hits(
            graph,
            request,
            mode=mode,
            projection_document_provider=projection_document_provider,
        )
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


def _answer_rank_tool_hits(
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    *,
    mode: str,
    projection_document_provider: ProjectionDocumentProvider | None,
) -> HarnessQueryResponse:
    documents = projection_document_provider() if projection_document_provider is not None else None
    result = answer_rank_tool_hits(
        graph,
        user_goal=request.user_goal,
        recent_tool_result=request.recent_tool_result,
        already_seen_node_ids=request.already_seen_node_ids,
        max_results=request.max_cards,
        projection_documents=documents,
    )
    return HarnessQueryResponse(
        status=result.status,
        intent_requested=request.mode or request.intent,
        intent_used=mode,
        intent_correction=None,
        cards=(),
        next_actions=tuple(
            HarnessNextAction(
                action_type="inspect_file",
                target=hit.path,
                reason="Ranked from raw tool output using graph grounding and candidate-local projection similarity.",
                priority="recommended" if index == 0 else "optional",
            )
            for index, hit in enumerate(result.ranked_hits[:3])
        ),
        trace=_trace_for_rank_tool_hits(result.as_dict()),
        warnings=result.warnings,
        mode_result=result.as_dict(),
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


def explicit_query_mode(request: HarnessQueryRequest) -> str:
    requested_mode = str(request.mode or "").strip()
    if requested_mode:
        return requested_mode
    intent = str(request.intent or "").strip()
    if intent in SUPPORTED_QUERY_MODES:
        return intent
    if intent == "tool_overlay":
        resolved = resolve_query_mode(
            intent=intent,
            recent_tool_result=request.recent_tool_result,
            questions=request.questions,
        )
        if resolved.mode_used == "rank_tool_hits":
            return resolved.mode_used
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
    }


def _trace_for_rank_tool_hits(mode_result: dict[str, object]) -> dict[str, object]:
    nodes: list[str] = []
    for hit in mode_result.get("ranked_hits", []):
        if not isinstance(hit, dict):
            continue
        if file_node_id := str(hit.get("file_node_id") or ""):
            nodes.append(file_node_id)
        for symbol_node_id in hit.get("symbol_node_ids", []):
            if symbol_node_id:
                nodes.append(str(symbol_node_id))
    return {
        "nodes": list(dict.fromkeys(nodes)),
        "edges": [],
        "versions": [],
        "occurrences": [],
    }


__all__ = ["answer_runtime_query", "explicit_query_mode"]
