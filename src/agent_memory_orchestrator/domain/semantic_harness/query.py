from __future__ import annotations

from .anchor_resolution import resolve_anchors
from .identity import harness_card_id
from .models import HarnessCard
from .models import HarnessNextAction
from .models import HarnessQueryRequest
from .models import HarnessQueryResponse
from .models import ResolvedAnchor
from .models import StructuralHarnessGraph


SUPPORTED_INTENTS = {"edit_plan", "file_context"}


def answer_structural_query(graph: StructuralHarnessGraph, request: HarnessQueryRequest) -> HarnessQueryResponse:
    intent_requested = str(request.intent or "").strip() or "file_context"
    intent_used = intent_requested if intent_requested in SUPPORTED_INTENTS else "file_context"
    warnings: list[str] = []
    if intent_requested != intent_used:
        warnings.append(f"unsupported_intent:{intent_requested}")

    anchors = resolve_anchors(graph, files=request.files, symbols=request.symbols)
    status = _status_for(anchors.resolved, anchors.unresolved)
    node_by_id = graph.node_by_id()
    cards: list[HarnessCard] = []
    seen_cards = set(request.already_seen_card_ids)
    for anchor in anchors.resolved:
        node = node_by_id.get(anchor.node_id)
        if node is None:
            continue
        card = _card_for_anchor(graph=graph, request=request, intent=intent_used, anchor=anchor)
        if card.card_id in seen_cards:
            continue
        cards.append(card)
        if len(cards) >= max(1, int(request.max_cards or 5)):
            break

    actions = tuple(_next_action_for_card(card) for card in cards)
    if status == "partial_structural":
        warnings.append("structural_only:no_work_history_or_semantic_reasoning_attached")
    if anchors.unresolved:
        warnings.append("unresolved_anchors:" + ",".join(anchors.unresolved))

    return HarnessQueryResponse(
        status=status,
        intent_requested=intent_requested,
        intent_used=intent_used,
        intent_correction=None,
        cards=tuple(cards),
        next_actions=actions,
        trace={
            "nodes": [anchor.node_id for anchor in anchors.resolved],
            "edges": [],
            "versions": [],
            "occurrences": [],
        },
        warnings=tuple(warnings),
    )


def _status_for(resolved: tuple[ResolvedAnchor, ...], unresolved: tuple[str, ...]) -> str:
    if resolved and unresolved:
        return "partial_coverage"
    if resolved:
        return "partial_structural"
    return "unavailable"


def _card_for_anchor(
    *,
    graph: StructuralHarnessGraph,
    request: HarnessQueryRequest,
    intent: str,
    anchor: ResolvedAnchor,
) -> HarnessCard:
    node = graph.node_by_id()[anchor.node_id]
    card_type = "next_file" if node.kind == "File" else "symbol_context"
    title = f"Inspect {node.label}"
    why = _why_for(node_kind=node.kind, node_label=node.label, reason=anchor.reason)
    evidence = ({"node_id": node.id, "kind": node.kind},)
    card_id = harness_card_id(graph.repo_id, request.session_id, intent, (node.id,))
    return HarnessCard(
        card_id=card_id,
        type=card_type,
        title=title,
        why=why,
        evidence=evidence,
        risk="Structural-only card; verify behavior in code before editing.",
        confidence=round(min(0.82, anchor.confidence * 0.8), 2),
        next_action=f"Open {node.metadata.get('path') or node.label} and inspect before editing.",
    )


def _why_for(*, node_kind: str, node_label: str, reason: str) -> str:
    if node_kind == "Symbol":
        return f"{node_label} resolved from an exact structural symbol anchor ({reason})."
    return f"{node_label} resolved from an exact structural file anchor ({reason})."


def _next_action_for_card(card: HarnessCard) -> HarnessNextAction:
    return HarnessNextAction(
        action_type="inspect_file",
        target=card.next_action.removeprefix("Open ").removesuffix(" and inspect before editing."),
        reason=card.why,
        priority="recommended",
    )


__all__ = ["answer_structural_query"]
