from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import HarnessCard
from agent_memory_orchestrator.domain.semantic_harness import HarnessNextAction
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryRequest
from agent_memory_orchestrator.domain.semantic_harness import HarnessQueryResponse
from agent_memory_orchestrator.domain.semantic_harness import StructuralHarnessGraph
from agent_memory_orchestrator.domain.semantic_harness import harness_card_id
from agent_memory_orchestrator.domain.semantic_harness import resolve_anchors


ENRICHABLE_TOOL_KINDS = {"apply_patch", "git_diff", "test_output"}
BROAD_SEARCH_FILE_THRESHOLD = 8


def enrich_tool_overlay_response(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    response: HarnessQueryResponse,
    tool_kind: str,
) -> HarnessQueryResponse:
    """Add concise tool-specific cards after generic graph grounding succeeds."""

    if tool_kind == "file_read":
        return _drop_redundant_file_read_cards(request=request, response=response)
    if tool_kind == "search":
        return _drop_broad_search_cards(request=request, response=response)
    if tool_kind not in ENRICHABLE_TOOL_KINDS:
        return response
    file_nodes = _grounded_file_nodes(graph, request)
    if not file_nodes:
        return response
    tool_card = _tool_card_for_files(graph=graph, request=request, tool_kind=tool_kind, file_nodes=file_nodes)
    if tool_card is None:
        return response
    if tool_card.card_id in set(request.already_seen_card_ids):
        return _append_warning(response, f"duplicate_tool_card:{tool_kind}")
    return _prepend_tool_card(response=response, request=request, tool_card=tool_card, tool_kind=tool_kind)


def _drop_redundant_file_read_cards(
    *,
    request: HarnessQueryRequest,
    response: HarnessQueryResponse,
) -> HarnessQueryResponse:
    anchored_files = set(request.files)
    if not anchored_files:
        return response
    kept = tuple(card for card in response.cards if not _is_same_file_read_card(card, anchored_files))
    if len(kept) == len(response.cards):
        return response
    return HarnessQueryResponse(
        status=response.status,
        intent_requested=response.intent_requested,
        intent_used=response.intent_used,
        intent_correction=response.intent_correction,
        cards=kept,
        next_actions=tuple(_next_action_for(card) for card in kept),
        trace=response.trace,
        warnings=tuple(dict.fromkeys((*response.warnings, "redundant_file_read_card"))),
    )


def _drop_broad_search_cards(
    *,
    request: HarnessQueryRequest,
    response: HarnessQueryResponse,
) -> HarnessQueryResponse:
    if len(request.files) < BROAD_SEARCH_FILE_THRESHOLD:
        return response
    kept = tuple(card for card in response.cards if not _is_exact_anchor_next_file_card(card, set(request.files)))
    if len(kept) == len(response.cards):
        return response
    return HarnessQueryResponse(
        status=response.status,
        intent_requested=response.intent_requested,
        intent_used=response.intent_used,
        intent_correction=response.intent_correction,
        cards=kept,
        next_actions=tuple(_next_action_for(card) for card in kept),
        trace=response.trace,
        warnings=tuple(dict.fromkeys((*response.warnings, "broad_search_anchor_only_card"))),
    )


def _is_same_file_read_card(card: HarnessCard, anchored_files: set[str]) -> bool:
    if card.type != "next_file":
        return False
    return _is_exact_anchor_next_file_card(card, anchored_files)


def _is_exact_anchor_next_file_card(card: HarnessCard, anchored_files: set[str]) -> bool:
    if card.type != "next_file":
        return False
    for evidence in card.evidence:
        if path := evidence.get("path"):
            return path in anchored_files
    if card.next_action.startswith("Open "):
        path = card.next_action.removeprefix("Open ").split(" and inspect", 1)[0]
        return path in anchored_files
    if card.title.startswith("Inspect "):
        return card.title.removeprefix("Inspect ") in anchored_files
    return False


def _grounded_file_nodes(graph: StructuralHarnessGraph, request: HarnessQueryRequest) -> tuple[object, ...]:
    resolved = resolve_anchors(graph, files=request.files, symbols=())
    node_by_id = graph.node_by_id()
    out: list[object] = []
    for anchor in resolved.resolved:
        if anchor.kind != "File":
            continue
        node = node_by_id.get(anchor.node_id)
        if node is not None:
            out.append(node)
    return tuple(out)


def _tool_card_for_files(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    tool_kind: str,
    file_nodes: tuple[object, ...],
) -> HarnessCard | None:
    visible_files = file_nodes[:3]
    if not visible_files:
        return None
    labels = tuple(str(getattr(node, "label", "")) for node in visible_files)
    target = labels[0]
    count_suffix = "" if len(file_nodes) == 1 else f" + {len(file_nodes) - 1} more"
    card_id = harness_card_id(graph.repo_id, request.session_id, request.intent, ("tool_context", tool_kind, *labels))
    evidence = tuple(
        {
            "node_id": str(getattr(node, "id", "")),
            "kind": str(getattr(node, "kind", "")),
            "path": str(getattr(node, "metadata", {}).get("path") or getattr(node, "label", "")),
        }
        for node in visible_files
    )
    if tool_kind == "test_output":
        if not request.errors:
            return None
        return HarnessCard(
            card_id=card_id,
            type="test_target",
            title=f"Inspect failing test anchor {target}{count_suffix}",
            why="Test output referenced graph-grounded files; use them as validation anchors before editing.",
            evidence=evidence,
            risk="Treat failing test output as the current validation signal; do not patch unrelated files first.",
            confidence=0.86,
            next_action=f"Open {target} and inspect the failing assertion or traceback.",
        )
    if tool_kind == "git_diff":
        return HarnessCard(
            card_id=card_id,
            type="risk",
            title=f"Review diff impact for {target}{count_suffix}",
            why="git diff changed graph-grounded files; verify immediate context before committing or broadening the edit.",
            evidence=evidence,
            risk="Diff output can hide dependent tests or imports; inspect touched files before finalizing.",
            confidence=0.8,
            next_action=f"Inspect {target} and run the smallest relevant validation.",
        )
    return HarnessCard(
        card_id=card_id,
        type="risk",
        title=f"Verify patch impact for {target}{count_suffix}",
        why="The patch updated graph-grounded files; verify local context and related tests before continuing.",
        evidence=evidence,
        risk="A successful patch only proves text changed; it does not prove behavior or dependencies are safe.",
        confidence=0.82,
        next_action=f"Inspect {target} after the patch and run targeted validation.",
    )


def _prepend_tool_card(
    *,
    response: HarnessQueryResponse,
    request: HarnessQueryRequest,
    tool_card: HarnessCard,
    tool_kind: str,
) -> HarnessQueryResponse:
    existing = tuple(card for card in response.cards if card.card_id != tool_card.card_id)
    cards = (tool_card, *existing)[: max(1, request.max_cards)]
    actions = (_next_action_for(tool_card), *response.next_actions)[: len(cards)]
    return HarnessQueryResponse(
        status=response.status,
        intent_requested=response.intent_requested,
        intent_used=response.intent_used,
        intent_correction=response.intent_correction,
        cards=cards,
        next_actions=actions,
        trace=_trace_with_card(response.trace, tool_card),
        warnings=tuple(dict.fromkeys((*response.warnings, f"tool_context_enriched:{tool_kind}"))),
    )


def _next_action_for(card: HarnessCard) -> HarnessNextAction:
    return HarnessNextAction(
        action_type="inspect_file",
        target=card.evidence[0].get("path") or card.evidence[0].get("node_id", card.title),
        reason=card.why,
        priority="recommended",
    )


def _trace_with_card(trace: dict[str, object], card: HarnessCard) -> dict[str, object]:
    out = {
        "nodes": list(trace.get("nodes") or []),
        "edges": list(trace.get("edges") or []),
        "versions": list(trace.get("versions") or []),
        "occurrences": list(trace.get("occurrences") or []),
    }
    for evidence in card.evidence:
        if node_id := evidence.get("node_id"):
            if node_id not in out["nodes"]:
                out["nodes"].append(node_id)
    return out


def _append_warning(response: HarnessQueryResponse, warning: str) -> HarnessQueryResponse:
    return HarnessQueryResponse(
        status=response.status,
        intent_requested=response.intent_requested,
        intent_used=response.intent_used,
        intent_correction=response.intent_correction,
        cards=response.cards,
        next_actions=response.next_actions,
        trace=response.trace,
        warnings=tuple(dict.fromkeys((*response.warnings, warning))),
    )


__all__ = ["enrich_tool_overlay_response"]
