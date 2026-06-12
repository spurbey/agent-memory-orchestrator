from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass(slots=True, frozen=True)
class SourceFile:
    path: str
    text: str
    language: str = ""


@dataclass(slots=True, frozen=True)
class HarnessNode:
    id: str
    kind: str
    label: str
    repo_id: str
    status: str = "active"
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class HarnessEdge:
    source_id: str
    target_id: str
    kind: str
    weight: float = 1.0
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class StructuralHarnessGraph:
    repo_id: str
    nodes: tuple[HarnessNode, ...]
    edges: tuple[HarnessEdge, ...]

    def node_by_id(self) -> dict[str, HarnessNode]:
        return {node.id: node for node in self.nodes}

    def nodes_by_kind(self, kind: str) -> tuple[HarnessNode, ...]:
        return tuple(node for node in self.nodes if node.kind == kind)

    def outgoing(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]:
        return tuple(
            edge
            for edge in self.edges
            if edge.source_id == node_id and (not kind or edge.kind == kind)
        )


@dataclass(slots=True, frozen=True)
class ResolvedAnchor:
    requested: str
    node_id: str
    kind: str
    confidence: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class HarnessQueryRequest:
    intent: str
    user_goal: str
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    commits: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    recent_tool_result: dict[str, Any] = field(default_factory=dict)
    max_cards: int = 5
    max_tokens: int = 900
    detail: str = "strict"
    session_id: str = ""
    already_seen_node_ids: tuple[str, ...] = ()
    already_seen_relation_ids: tuple[str, ...] = ()
    already_seen_card_ids: tuple[str, ...] = ()


@dataclass(slots=True, frozen=True)
class HarnessCard:
    card_id: str
    type: str
    title: str
    why: str
    evidence: tuple[dict[str, str], ...]
    risk: str
    confidence: float
    next_action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "type": self.type,
            "title": self.title,
            "why": self.why,
            "evidence": list(self.evidence),
            "risk": self.risk,
            "confidence": self.confidence,
            "next_action": self.next_action,
        }


@dataclass(slots=True, frozen=True)
class HarnessNextAction:
    action_type: str
    target: str
    reason: str
    priority: str = "recommended"

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class HarnessQueryResponse:
    status: str
    intent_requested: str
    intent_used: str
    intent_correction: dict[str, Any] | None
    cards: tuple[HarnessCard, ...]
    next_actions: tuple[HarnessNextAction, ...]
    trace: dict[str, Any]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "intent_requested": self.intent_requested,
            "intent_used": self.intent_used,
            "intent_correction": self.intent_correction,
            "cards": [card.as_dict() for card in self.cards],
            "next_actions": [action.as_dict() for action in self.next_actions],
            "trace": self.trace,
            "warnings": list(self.warnings),
        }


def _asdict(obj: Any) -> dict[str, Any]:
    return {field_name: getattr(obj, field_name) for field_name in obj.__dataclass_fields__}  # type: ignore[attr-defined]


__all__ = [
    "HarnessCard",
    "HarnessEdge",
    "HarnessNextAction",
    "HarnessNode",
    "HarnessQueryRequest",
    "HarnessQueryResponse",
    "ResolvedAnchor",
    "SourceFile",
    "StructuralHarnessGraph",
]
