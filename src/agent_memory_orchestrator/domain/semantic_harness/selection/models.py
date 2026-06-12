from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..models import HarnessCard


@dataclass(slots=True, frozen=True)
class CardSelectionOptions:
    max_cards: int = 5
    max_tokens: int = 900
    already_seen_card_ids: tuple[str, ...] = ()
    already_seen_node_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class SuppressedCard:
    card_id: str
    title: str
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"card_id": self.card_id, "title": self.title, "reason": self.reason}


@dataclass(slots=True, frozen=True)
class CardSelectionResult:
    selected: tuple[HarnessCard, ...]
    suppressed: tuple[SuppressedCard, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "selected": [card.as_dict() for card in self.selected],
            "suppressed": [item.as_dict() for item in self.suppressed],
        }


__all__ = ["CardSelectionOptions", "CardSelectionResult", "SuppressedCard"]
