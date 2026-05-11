from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from .models import CodeNode
from .models import DecisionThread
from .models import DecisionUnit
from .models import TestRun


@dataclass(slots=True, frozen=True)
class ReasoningEdge:
    source_id: str
    target_id: str
    kind: str
    confidence: float
    evidence_ids: tuple[str, ...]
    metadata: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "metadata": self.metadata or {},
        }


@dataclass(slots=True, frozen=True)
class ValidationLinkResult:
    decision: DecisionUnit
    edges: tuple[ReasoningEdge, ...]
    diagnostics: tuple[str, ...] = ()


def produced_change_edges(
    *,
    decisions: list[DecisionUnit],
    code_nodes: list[CodeNode],
    thread: DecisionThread,
) -> tuple[ReasoningEdge, ...]:
    thread_files = {_norm(path) for path in thread.file_paths}
    edges: list[ReasoningEdge] = []
    for decision in decisions:
        for code_node in code_nodes:
            if thread_files and not any(_same_path(path, code_node.file_path) for path in thread_files):
                continue
            evidence_ids = _dedupe((*decision.evidence_ids, *code_node.evidence_ids))
            edges.append(
                ReasoningEdge(
                    source_id=decision.id,
                    target_id=code_node.id,
                    kind="PRODUCED_CHANGE_IN",
                    confidence=min(decision.confidence, 0.85),
                    evidence_ids=evidence_ids,
                    metadata={"thread_id": thread.id, "file_path": code_node.file_path},
                )
            )
    return tuple(edges)


def validation_edges_for_test(
    *,
    decision: DecisionUnit,
    test_run: TestRun,
    event_order: dict[str, int],
    write_event_ids: tuple[str, ...],
) -> ValidationLinkResult:
    diagnostics: list[str] = []
    test_event_id = str(test_run.metadata.get("event_id") or "")
    test_index = event_order.get(test_event_id)
    write_indexes = [event_order[event_id] for event_id in write_event_ids if event_id in event_order]
    if test_index is None:
        return ValidationLinkResult(decision=decision, edges=(), diagnostics=("missing_test_event_order",))
    if not write_indexes:
        return ValidationLinkResult(decision=decision, edges=(), diagnostics=("missing_write_event_order",))
    if test_index <= max(write_indexes):
        return ValidationLinkResult(decision=decision, edges=(), diagnostics=("test_before_write",))

    result = test_run.result.lower().strip()
    evidence_ids = _dedupe((*decision.evidence_ids, *test_run.evidence_ids))
    if result == "pass":
        edge = ReasoningEdge(
            source_id=decision.id,
            target_id=test_run.id,
            kind="VALIDATED_BY",
            confidence=0.9,
            evidence_ids=evidence_ids,
            metadata={"test_event_id": test_event_id, "write_event_ids": list(write_event_ids)},
        )
        bumped = replace(decision, confidence=max(decision.confidence, 0.9))
        return ValidationLinkResult(decision=bumped, edges=(edge,))
    if result == "fail":
        edge = ReasoningEdge(
            source_id=decision.id,
            target_id=test_run.id,
            kind="FAILED_VALIDATION",
            confidence=0.9,
            evidence_ids=evidence_ids,
            metadata={"test_event_id": test_event_id, "write_event_ids": list(write_event_ids)},
        )
        return ValidationLinkResult(decision=decision, edges=(edge,))
    diagnostics.append(f"unknown_test_result:{test_run.result}")
    return ValidationLinkResult(decision=decision, edges=(), diagnostics=tuple(diagnostics))


def _norm(value: str) -> str:
    return value.replace("\\", "/").strip().strip('"').lower()


def _same_path(left: str, right: str) -> bool:
    left_norm = _norm(left)
    right_norm = _norm(right)
    return left_norm == right_norm or left_norm.endswith(f"/{right_norm}") or right_norm.endswith(f"/{left_norm}")


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return tuple(out)
