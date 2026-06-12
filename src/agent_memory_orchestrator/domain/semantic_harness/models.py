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
    _node_by_id: dict[str, HarnessNode] = field(init=False, repr=False, compare=False)
    _nodes_by_kind: dict[str, tuple[HarnessNode, ...]] = field(init=False, repr=False, compare=False)
    _outgoing_by_source: dict[str, tuple[HarnessEdge, ...]] = field(init=False, repr=False, compare=False)
    _outgoing_by_source_kind: dict[tuple[str, str], tuple[HarnessEdge, ...]] = field(init=False, repr=False, compare=False)
    _incoming_by_target: dict[str, tuple[HarnessEdge, ...]] = field(init=False, repr=False, compare=False)
    _incoming_by_target_kind: dict[tuple[str, str], tuple[HarnessEdge, ...]] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        nodes_by_kind: dict[str, list[HarnessNode]] = {}
        outgoing_by_source: dict[str, list[HarnessEdge]] = {}
        outgoing_by_source_kind: dict[tuple[str, str], list[HarnessEdge]] = {}
        incoming_by_target: dict[str, list[HarnessEdge]] = {}
        incoming_by_target_kind: dict[tuple[str, str], list[HarnessEdge]] = {}

        for node in self.nodes:
            nodes_by_kind.setdefault(node.kind, []).append(node)
        for edge in self.edges:
            outgoing_by_source.setdefault(edge.source_id, []).append(edge)
            outgoing_by_source_kind.setdefault((edge.source_id, edge.kind), []).append(edge)
            incoming_by_target.setdefault(edge.target_id, []).append(edge)
            incoming_by_target_kind.setdefault((edge.target_id, edge.kind), []).append(edge)

        object.__setattr__(self, "_node_by_id", {node.id: node for node in self.nodes})
        object.__setattr__(self, "_nodes_by_kind", _freeze_node_index(nodes_by_kind))
        object.__setattr__(self, "_outgoing_by_source", _freeze_edge_index(outgoing_by_source))
        object.__setattr__(self, "_outgoing_by_source_kind", _freeze_edge_index(outgoing_by_source_kind))
        object.__setattr__(self, "_incoming_by_target", _freeze_edge_index(incoming_by_target))
        object.__setattr__(self, "_incoming_by_target_kind", _freeze_edge_index(incoming_by_target_kind))

    def node_by_id(self) -> dict[str, HarnessNode]:
        return self._node_by_id

    def nodes_by_kind(self, kind: str) -> tuple[HarnessNode, ...]:
        return self._nodes_by_kind.get(kind, ())

    def outgoing(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]:
        if kind:
            return self._outgoing_by_source_kind.get((node_id, kind), ())
        return self._outgoing_by_source.get(node_id, ())

    def incoming(self, node_id: str, *, kind: str = "") -> tuple[HarnessEdge, ...]:
        if kind:
            return self._incoming_by_target_kind.get((node_id, kind), ())
        return self._incoming_by_target.get(node_id, ())


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


def _freeze_node_index(index: dict[str, list[HarnessNode]]) -> dict[str, tuple[HarnessNode, ...]]:
    return {key: tuple(value) for key, value in index.items()}


def _freeze_edge_index(index: dict[Any, list[HarnessEdge]]) -> dict[Any, tuple[HarnessEdge, ...]]:
    return {key: tuple(value) for key, value in index.items()}


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
