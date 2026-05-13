from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from .models import DecisionThread
from .models import DecisionUnit
from .models import ExtractionRun
from .models import TimelineEvent
from .validation import validate_graph_object


QWEN_DECISION_THRESHOLD = 0.70


class QwenDecisionExtractor(Protocol):
    def extract(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return the decision_extraction_fallback JSON payload."""


@dataclass(slots=True, frozen=True)
class DecisionExtractionResult:
    decisions: tuple[DecisionUnit, ...]
    review_candidates: tuple[dict[str, Any], ...] = ()
    diagnostics: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "decisions": [decision.as_dict() for decision in self.decisions],
            "review_candidates": list(self.review_candidates),
            "diagnostics": list(self.diagnostics),
        }


@dataclass(slots=True, frozen=True)
class _Rule:
    name: str
    pattern: re.Pattern[str]
    decision_type: str
    kind: str
    confidence: float


RULES = (
    _Rule(
        name="planned_action",
        pattern=re.compile(r"\bI(?:'ll| will)\s+(?P<action>.+?)\s+because\s+(?P<reason>.+?)(?:[.!?]|$)", re.IGNORECASE),
        decision_type="planned_action",
        kind="Decision",
        confidence=0.60,
    ),
    _Rule(
        name="completed_fix",
        pattern=re.compile(r"\bFixed by\s+(?P<action>.+?)(?:[.!?]|$)", re.IGNORECASE),
        decision_type="completed_fix",
        kind="Fix",
        confidence=0.80,
    ),
    _Rule(
        name="investigation_result",
        pattern=re.compile(r"\bThe issue is\s+(?P<cause>.+?)(?:[.!?]|$)", re.IGNORECASE),
        decision_type="investigation_result",
        kind="Bug",
        confidence=0.60,
    ),
    _Rule(
        name="constraint",
        pattern=re.compile(r"\bPinning\s+(?P<subject>.+?)\s+to\s+(?P<object>.+?)(?:[.!?]|$)", re.IGNORECASE),
        decision_type="constraint",
        kind="Decision",
        confidence=0.75,
    ),
    _Rule(
        name="revert",
        pattern=re.compile(r"\bReverting\s+(?P<subject>.+?)(?:[.!?]|$)", re.IGNORECASE),
        decision_type="revert",
        kind="Fix",
        confidence=0.85,
    ),
)


def extract_decisions(
    *,
    thread: DecisionThread,
    events: list[TimelineEvent],
    extraction_run: ExtractionRun,
    qwen: QwenDecisionExtractor | None = None,
) -> DecisionExtractionResult:
    event_by_id = {event.id: event for event in events}
    thread_events = [event_by_id[event_id] for event_id in thread.event_ids if event_id in event_by_id]
    decisions: list[DecisionUnit] = []
    diagnostics: list[dict[str, Any]] = []
    review_candidates: list[dict[str, Any]] = []

    for event in thread_events:
        if event.event_type != "agent_message":
            continue
        decisions.extend(_deterministic_decisions(event, thread=thread, extraction_run=extraction_run, offset=len(decisions)))

    if not decisions and qwen is not None and _has_durable_signals(thread, thread_events):
        qwen_result = _qwen_fallback(thread, thread_events, extraction_run, qwen)
        decisions.extend(qwen_result.decisions)
        diagnostics.extend(qwen_result.diagnostics)
        review_candidates.extend(qwen_result.review_candidates)

    return DecisionExtractionResult(
        decisions=tuple(decisions),
        review_candidates=tuple(review_candidates),
        diagnostics=tuple(diagnostics),
    )


def build_decision_extraction_payload(
    *,
    thread: DecisionThread,
    events: list[TimelineEvent],
    extraction_run: ExtractionRun,
) -> dict[str, Any]:
    event_by_id = {event.id: event for event in events}
    thread_events = [event_by_id[event_id] for event_id in thread.event_ids if event_id in event_by_id]
    return {
        "session_id": thread.session_id,
        "extraction_run_id": extraction_run.id,
        "thread_id": thread.id,
        "messages": [
            {"role": _role_for_event(event), "text": event.content[:2000], "event_id": event.id}
            for event in thread_events
            if event.content and event.event_type in {"user_message", "agent_message", "tool_use", "tool_result", "post_tool_use"}
        ],
        "code_nodes": [],
        "tests": [],
    }


def _deterministic_decisions(
    event: TimelineEvent,
    *,
    thread: DecisionThread,
    extraction_run: ExtractionRun,
    offset: int,
) -> list[DecisionUnit]:
    decisions: list[DecisionUnit] = []
    for rule in RULES:
        for match in rule.pattern.finditer(event.content):
            summary = _summary_for(rule, match)
            evidence_ids = _evidence_ids(event, thread, extraction_run)
            decision = DecisionUnit(
                id=f"decision:{thread.id}:{offset + len(decisions) + 1}",
                session_id=thread.session_id,
                extraction_run_id=extraction_run.id,
                summary=summary,
                evidence_ids=evidence_ids,
                kind=rule.kind,
                confidence=rule.confidence,
                source="deterministic",
                metadata={
                    "decision_type": rule.decision_type,
                    "rule": rule.name,
                    "event_id": event.id,
                    "thread_id": thread.id,
                },
            )
            report = validate_graph_object(decision, qwen_threshold=QWEN_DECISION_THRESHOLD)
            if report.ok:
                decisions.append(decision)
    return decisions


def _qwen_fallback(
    thread: DecisionThread,
    thread_events: list[TimelineEvent],
    extraction_run: ExtractionRun,
    qwen: QwenDecisionExtractor,
) -> DecisionExtractionResult:
    payload = build_decision_extraction_payload(thread=thread, events=thread_events, extraction_run=extraction_run)
    try:
        raw = qwen.extract(payload)
    except Exception as exc:  # noqa: BLE001 - Qwen failures are diagnostics, not crashes.
        return DecisionExtractionResult(decisions=(), diagnostics=({"call": "decision_extraction_fallback", "error_type": type(exc).__name__},))
    if not isinstance(raw, dict) or not isinstance(raw.get("decisions"), list):
        return DecisionExtractionResult(
            decisions=(),
            diagnostics=({"call": "decision_extraction_fallback", "error_type": "schema_mismatch"},),
        )

    decisions: list[DecisionUnit] = []
    review: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for index, item in enumerate(raw["decisions"], start=1):
        if not isinstance(item, dict):
            diagnostics.append({"call": "decision_extraction_fallback", "error_type": "schema_mismatch", "index": index})
            continue
        missing = [field_name for field_name in ("decision_type", "subject", "predicate", "object", "reason", "confidence") if field_name not in item]
        if missing:
            diagnostics.append(
                {
                    "call": "decision_extraction_fallback",
                    "error_type": "schema_mismatch",
                    "index": index,
                    "missing": missing,
                }
            )
            continue
        confidence = _float(item.get("confidence"), default=0.0)
        if confidence < QWEN_DECISION_THRESHOLD:
            review.append({"reason": "low_confidence", "candidate": item, "threshold": QWEN_DECISION_THRESHOLD})
            continue
        kind = _kind_for_qwen_type(str(item.get("decision_type") or ""))
        decision = DecisionUnit(
            id=f"decision:{thread.id}:qwen:{index}",
            session_id=thread.session_id,
            extraction_run_id=extraction_run.id,
            summary=_qwen_summary(item),
            evidence_ids=_evidence_ids_from_qwen(
                item,
                thread,
                extraction_run,
                allowed_event_ids={event.id for event in thread_events},
            ),
            kind=kind,
            confidence=confidence,
            source="qwen",
            qwen_call="decision_extraction_fallback",
            metadata={"thread_id": thread.id, "decision_type": item.get("decision_type"), "reason": item.get("reason")},
        )
        report = validate_graph_object(decision, qwen_threshold=QWEN_DECISION_THRESHOLD)
        if report.ok:
            decisions.append(decision)
        else:
            review.append({"reason": "validation_failed", "candidate": item, "validation": report.as_dict()})
    return DecisionExtractionResult(decisions=tuple(decisions), review_candidates=tuple(review), diagnostics=tuple(diagnostics))


def _summary_for(rule: _Rule, match: re.Match[str]) -> str:
    groups = match.groupdict()
    if rule.decision_type == "planned_action":
        return f"{groups.get('action', '').strip()} because {groups.get('reason', '').strip()}"
    if rule.decision_type == "completed_fix":
        return f"Fixed by {groups.get('action', '').strip()}"
    if rule.decision_type == "investigation_result":
        return f"The issue is {groups.get('cause', '').strip()}"
    if rule.decision_type == "constraint":
        return f"Pinning {groups.get('subject', '').strip()} to {groups.get('object', '').strip()}"
    if rule.decision_type == "revert":
        return f"Reverting {groups.get('subject', '').strip()}"
    return match.group(0).strip()


def _has_durable_signals(thread: DecisionThread, events: list[TimelineEvent]) -> bool:
    return bool(thread.file_paths or any(event.event_type in {"tool_use", "tool_result", "post_tool_use"} for event in events))


def _role_for_event(event: TimelineEvent) -> str:
    if event.event_type == "agent_message":
        return "assistant"
    if event.event_type == "user_message":
        return "user"
    return "tool"


def _evidence_ids(event: TimelineEvent, thread: DecisionThread, extraction_run: ExtractionRun) -> tuple[str, ...]:
    values = []
    if event.evidence_id:
        values.append(event.evidence_id)
    values.extend(thread.evidence_ids)
    values.extend(extraction_run.evidence_ids)
    return _dedupe(values)


def _evidence_ids_from_qwen(
    item: dict[str, Any],
    thread: DecisionThread,
    extraction_run: ExtractionRun,
    *,
    allowed_event_ids: set[str],
) -> tuple[str, ...]:
    values = []
    raw_ids = item.get("evidence_event_ids")
    if isinstance(raw_ids, list):
        values.extend(str(value) for value in raw_ids if str(value) in allowed_event_ids or str(value).startswith("raw_"))
    values.extend(thread.evidence_ids)
    values.extend(extraction_run.evidence_ids)
    return _dedupe(values)


def _kind_for_qwen_type(decision_type: str) -> str:
    if decision_type == "completed_fix":
        return "Fix"
    if decision_type == "investigation_result":
        return "Bug"
    if decision_type == "open_question":
        return "OpenQuestion"
    return "Decision"


def _qwen_summary(item: dict[str, Any]) -> str:
    subject = str(item.get("subject") or "").strip()
    predicate = str(item.get("predicate") or "").strip()
    obj = str(item.get("object") or "").strip()
    reason = str(item.get("reason") or "").strip()
    summary = " ".join(part for part in (subject, predicate, obj) if part)
    if reason:
        summary = f"{summary} because {reason}" if summary else reason
    return summary[:600]


def _dedupe(values: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return tuple(out)


def _float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
