from __future__ import annotations

from ..models import HarnessCard
from .models import CardSelectionOptions
from .models import CardSelectionResult
from .models import SuppressedCard


def select_harness_cards(
    candidates: tuple[HarnessCard, ...],
    *,
    options: CardSelectionOptions = CardSelectionOptions(),
) -> CardSelectionResult:
    """Select compact, novel cards under card/token budget."""

    max_cards = max(0, int(options.max_cards or 0))
    max_tokens = max(0, int(options.max_tokens or 0))
    seen_card_ids = set(options.already_seen_card_ids)
    seen_node_ids = set(options.already_seen_node_ids)
    selected: list[HarnessCard] = []
    suppressed: list[SuppressedCard] = []
    selected_node_ids: set[str] = set()
    used_tokens = 0
    for card in sorted(candidates, key=_selection_key):
        support_nodes = _support_node_ids(card)
        if card.card_id in seen_card_ids:
            suppressed.append(SuppressedCard(card_id=card.card_id, title=card.title, reason="already_seen_card"))
            continue
        if support_nodes and support_nodes <= seen_node_ids:
            suppressed.append(SuppressedCard(card_id=card.card_id, title=card.title, reason="already_seen_nodes"))
            continue
        if support_nodes and support_nodes <= selected_node_ids:
            suppressed.append(SuppressedCard(card_id=card.card_id, title=card.title, reason="duplicate_selected_nodes"))
            continue
        token_cost = _estimated_tokens(card)
        if len(selected) >= max_cards:
            suppressed.append(SuppressedCard(card_id=card.card_id, title=card.title, reason="max_cards"))
            continue
        if max_tokens and used_tokens + token_cost > max_tokens and selected:
            suppressed.append(SuppressedCard(card_id=card.card_id, title=card.title, reason="max_tokens"))
            continue
        selected.append(card)
        selected_node_ids.update(support_nodes)
        used_tokens += token_cost
    return CardSelectionResult(selected=tuple(selected), suppressed=tuple(suppressed))


def card_selection_score(card: HarnessCard) -> float:
    route = _route_priority(card)
    card_type = _card_type_priority(card)
    confidence = max(0.0, min(1.0, float(card.confidence or 0.0)))
    evidence = min(1.0, len(card.evidence) / 4.0)
    return round(route * 0.45 + card_type * 0.25 + confidence * 0.22 + evidence * 0.08, 6)


def _selection_key(card: HarnessCard) -> tuple[float, int, str, str]:
    return (-card_selection_score(card), _card_type_rank(card), card.title, card.card_id)


def _route_priority(card: HarnessCard) -> float:
    if _is_vector_card(card):
        return 0.38
    if _is_lexical_card(card):
        return 0.5
    if _is_exact_anchor_card(card):
        return 1.0
    if card.type == "doc_support":
        return 0.82
    if card.type == "historical_relation":
        return 0.72
    if card.type == "dependency":
        return 0.68
    return 0.6


def _card_type_priority(card: HarnessCard) -> float:
    return {
        "risk": 1.0,
        "test_target": 0.92,
        "next_file": 0.88,
        "symbol_context": 0.82,
        "historical_relation": 0.78,
        "dependency": 0.74,
        "doc_support": 0.7,
    }.get(card.type, 0.5)


def _card_type_rank(card: HarnessCard) -> int:
    return {
        "risk": 0,
        "test_target": 1,
        "next_file": 2,
        "symbol_context": 3,
        "historical_relation": 4,
        "dependency": 5,
        "doc_support": 6,
    }.get(card.type, 9)


def _is_exact_anchor_card(card: HarnessCard) -> bool:
    return (
        len(card.evidence) == 1
        and bool(card.evidence[0].get("node_id"))
        and card.evidence[0].get("kind") in {"File", "Symbol"}
        and card.type in {"next_file", "symbol_context"}
    )


def _is_lexical_card(card: HarnessCard) -> bool:
    return any(evidence.get("kind") == "ProjectionDocument" and not evidence.get("retrieval_source") for evidence in card.evidence)


def _is_vector_card(card: HarnessCard) -> bool:
    return any(evidence.get("retrieval_source") == "vector" for evidence in card.evidence)


def _support_node_ids(card: HarnessCard) -> set[str]:
    return {node_id for evidence in card.evidence if (node_id := evidence.get("node_id"))}


def _estimated_tokens(card: HarnessCard) -> int:
    text = " ".join(
        (
            card.title,
            card.why,
            card.risk,
            card.next_action,
            " ".join(" ".join(str(value) for value in evidence.values()) for evidence in card.evidence),
        )
    )
    return max(12, len(text) // 4)


__all__ = ["card_selection_score", "select_harness_cards"]
