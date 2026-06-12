from __future__ import annotations

from agent_memory_orchestrator.domain.semantic_harness import CardSelectionOptions
from agent_memory_orchestrator.domain.semantic_harness import HarnessCard
from agent_memory_orchestrator.domain.semantic_harness import card_selection_score
from agent_memory_orchestrator.domain.semantic_harness import select_harness_cards


def test_selector_prioritizes_exact_anchor_over_vector_candidate() -> None:
    exact = _card(
        "c:exact",
        "next_file",
        "Inspect src/auth.py",
        ({"node_id": "file:repo:test:src/auth.py", "kind": "File"},),
        confidence=0.72,
    )
    vector = _card(
        "c:vector",
        "symbol_context",
        "Inspect sign_in_user",
        (
            {"node_id": "symbol:repo:test:src/auth.py:sign_in_user:function", "kind": "Symbol"},
            {"kind": "ProjectionDocument", "retrieval_source": "vector", "score": "0.9300"},
        ),
        confidence=0.62,
    )

    result = select_harness_cards((vector, exact), options=CardSelectionOptions(max_cards=1))

    assert result.selected == (exact,)
    assert result.suppressed[0].reason == "max_cards"
    assert card_selection_score(exact) > card_selection_score(vector)


def test_selector_suppresses_seen_cards_and_nodes() -> None:
    seen_card = _card("c:seen", "next_file", "Inspect seen", ({"node_id": "file:seen", "kind": "File"},))
    seen_node = _card("c:seen-node", "next_file", "Inspect seen node", ({"node_id": "file:old", "kind": "File"},))
    fresh = _card("c:fresh", "next_file", "Inspect fresh", ({"node_id": "file:fresh", "kind": "File"},))

    result = select_harness_cards(
        (seen_card, seen_node, fresh),
        options=CardSelectionOptions(
            max_cards=3,
            already_seen_card_ids=("c:seen",),
            already_seen_node_ids=("file:old",),
        ),
    )

    assert result.selected == (fresh,)
    assert {item.reason for item in result.suppressed} == {"already_seen_card", "already_seen_nodes"}


def test_selector_suppresses_duplicate_selected_nodes() -> None:
    first = _card("c:first", "symbol_context", "Inspect login", ({"node_id": "symbol:login", "kind": "Symbol"},), confidence=0.8)
    duplicate = _card("c:duplicate", "symbol_context", "Inspect login again", ({"node_id": "symbol:login", "kind": "Symbol"},), confidence=0.7)

    result = select_harness_cards((duplicate, first), options=CardSelectionOptions(max_cards=2))

    assert result.selected == (first,)
    assert result.suppressed[0].card_id == "c:duplicate"
    assert result.suppressed[0].reason == "duplicate_selected_nodes"


def _card(
    card_id: str,
    card_type: str,
    title: str,
    evidence: tuple[dict[str, str], ...],
    *,
    confidence: float = 0.8,
) -> HarnessCard:
    return HarnessCard(
        card_id=card_id,
        type=card_type,
        title=title,
        why="test card",
        evidence=evidence,
        risk="",
        confidence=confidence,
        next_action="Inspect target.",
    )
