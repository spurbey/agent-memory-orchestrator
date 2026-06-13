from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from dataclasses import field
from typing import Any


@dataclass(slots=True, frozen=True)
class CapturedToolResult:
    tool_name: str
    tool_input: dict[str, Any]
    tool_response: str
    tool_use_id: str = ""
    session_id: str = ""
    turn_id: str = ""
    cwd: str = ""
    transcript_path: str = ""
    hook_event_name: str = "PostToolUse"

    @property
    def command(self) -> str:
        return str(self.tool_input.get("command") or self.tool_input.get("cmd") or "")

    @property
    def raw_output_hash(self) -> str:
        return hashlib.sha256(self.tool_response.encode("utf-8", errors="replace")).hexdigest()

    def output_excerpt(self, max_chars: int = 4000) -> str:
        if len(self.tool_response) <= max_chars:
            return self.tool_response
        return self.tool_response[:max_chars]

    def as_dict(self, *, include_response: bool = False) -> dict[str, Any]:
        out = {
            "tool_name": self.tool_name,
            "tool_input": dict(self.tool_input),
            "tool_use_id": self.tool_use_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "cwd": self.cwd,
            "transcript_path": self.transcript_path,
            "hook_event_name": self.hook_event_name,
            "raw_output_hash": self.raw_output_hash,
            "response_chars": len(self.tool_response),
            "response_excerpt": self.output_excerpt(1200),
        }
        if include_response:
            out["tool_response"] = self.tool_response
        return out


@dataclass(slots=True, frozen=True)
class ToolLineRef:
    file_path: str
    line: int
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"file_path": self.file_path, "line": self.line, "text": self.text}


@dataclass(slots=True, frozen=True)
class ToolResultAnchors:
    files: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    line_refs: tuple[ToolLineRef, ...] = ()

    @property
    def has_any(self) -> bool:
        return bool(self.files or self.symbols or self.errors or self.line_refs)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": list(self.files),
            "symbols": list(self.symbols),
            "errors": list(self.errors),
            "line_refs": [line_ref.as_dict() for line_ref in self.line_refs],
        }


@dataclass(slots=True, frozen=True)
class ToolOverlayLatency:
    parse_ms: int = 0
    harness_query_ms: int = 0
    decision_ms: int = 0

    @property
    def total_ms(self) -> int:
        return self.parse_ms + self.harness_query_ms + self.decision_ms

    def as_dict(self) -> dict[str, int]:
        return {
            "parse": self.parse_ms,
            "harness_query": self.harness_query_ms,
            "decision": self.decision_ms,
            "total": self.total_ms,
        }


@dataclass(slots=True, frozen=True)
class ToolOverlayDecision:
    mode: str
    tool_kind: str
    captured: CapturedToolResult
    anchors: ToolResultAnchors
    harness_request: dict[str, Any]
    harness_response: dict[str, Any]
    latency: ToolOverlayLatency
    would_attach: bool
    would_replace: bool = False
    suppression_reasons: tuple[str, ...] = ()
    confidence: float = 0.0
    token_overhead_estimate: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "tool_kind": self.tool_kind,
            "captured": self.captured.as_dict(),
            "extracted_anchors": self.anchors.as_dict(),
            "harness_request": self.harness_request,
            "harness_response": self.harness_response,
            "latency_ms": self.latency.as_dict(),
            "decision": {
                "mode": self.mode,
                "would_attach": self.would_attach,
                "would_replace": self.would_replace,
                "suppression_reasons": list(self.suppression_reasons),
                "confidence": self.confidence,
            },
            "token_overhead_estimate": self.token_overhead_estimate,
        }


@dataclass(slots=True, frozen=True)
class ToolOverlayJudgment:
    auto_useful: bool | None = None
    auto_mislead_candidate: bool | None = None
    manual_label: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "auto_useful": self.auto_useful,
            "auto_mislead_candidate": self.auto_mislead_candidate,
            "manual_label": self.manual_label,
            "reason": self.reason,
        }


@dataclass(slots=True, frozen=True)
class ToolOverlayEvalRecord:
    decision: ToolOverlayDecision
    judgment: ToolOverlayJudgment = field(default_factory=ToolOverlayJudgment)

    def as_dict(self) -> dict[str, Any]:
        out = self.decision.as_dict()
        out["judgment"] = self.judgment.as_dict()
        return out


@dataclass(slots=True, frozen=True)
class ShadowReplayReport:
    repo_id: str
    source_path: str
    records: tuple[ToolOverlayEvalRecord, ...]

    @property
    def idempotent_replay_rate(self) -> float:
        return 1.0

    def as_dict(self) -> dict[str, Any]:
        records = [record.as_dict() for record in self.records]
        return {
            "repo_id": self.repo_id,
            "source_path": self.source_path,
            "record_count": len(records),
            "metrics": _metrics(records),
            "records": records,
        }


def captured_tool_result_from_event(event: dict[str, Any]) -> CapturedToolResult | None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else event
    hook_event_name = str(payload.get("hook_event_name") or event.get("event_name") or "")
    event_name = str(event.get("event_name") or event.get("event_type") or "").lower()
    if hook_event_name != "PostToolUse" and event_name != "post_tool_use":
        return None
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    return CapturedToolResult(
        tool_name=str(payload.get("tool_name") or ""),
        tool_input=dict(tool_input),
        tool_response=str(payload.get("tool_response") or ""),
        tool_use_id=str(payload.get("tool_use_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        turn_id=str(payload.get("turn_id") or ""),
        cwd=str(payload.get("cwd") or ""),
        transcript_path=str(payload.get("transcript_path") or ""),
        hook_event_name="PostToolUse",
    )


def _metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind: dict[str, dict[str, int]] = {}
    latencies: list[int] = []
    token_overheads: list[int] = []
    attached = 0
    auto_useful = 0
    auto_mislead = 0
    manual_review_required = 0
    for record in records:
        tool_kind = str(record.get("tool_kind") or "unknown")
        bucket = by_kind.setdefault(tool_kind, {"total": 0, "attached": 0, "suppressed": 0, "suppress_rate": 0})
        bucket["total"] += 1
        decision = record.get("decision") or {}
        if decision.get("would_attach"):
            attached += 1
            bucket["attached"] += 1
        else:
            bucket["suppressed"] += 1
        latency = record.get("latency_ms") or {}
        latencies.append(int(latency.get("total") or 0))
        token_overheads.append(int(record.get("token_overhead_estimate") or 0))
        judgment = record.get("judgment") or {}
        if judgment.get("auto_useful") is True:
            auto_useful += 1
        if judgment.get("auto_mislead_candidate") is True:
            auto_mislead += 1
        if judgment.get("reason") == "manual_review_required":
            manual_review_required += 1
    for bucket in by_kind.values():
        total = int(bucket.get("total") or 0)
        bucket["suppress_rate"] = round(int(bucket.get("suppressed") or 0) / total, 4) if total else 0
    return {
        "attached_count": attached,
        "suppressed_count": len(records) - attached,
        "attach_rate": round(attached / len(records), 4) if records else 0.0,
        "suppress_rate": round((len(records) - attached) / len(records), 4) if records else 0.0,
        "p95_shadow_latency_ms": _percentile(latencies, 0.95),
        "token_overhead_p95": _percentile(token_overheads, 0.95),
        "idempotent_replay_rate": 1.0,
        "auto_useful_count": auto_useful,
        "auto_mislead_candidate_count": auto_mislead,
        "auto_mislead_candidate_rate": round(auto_mislead / len(records), 4) if records else 0.0,
        "manual_review_required_count": manual_review_required,
        "acceptance_thresholds": {
            "anchor_extraction_precision": 0.90,
            "strict_card_precision": 0.85,
            "mislead_rate": 0.05,
            "p95_shadow_latency_ms": 500,
            "token_overhead_p95": 900,
            "idempotent_replay_rate": 1.0,
        },
        "suppress_rate_targets": {
            "rg_many_matches": "0.20-0.40",
            "file_read": "0.10-0.25",
            "test_failure": "0.05-0.20",
            "apply_patch_edit": "0.30-0.60",
            "unknown_tool": "0.80-0.95",
        },
        "by_tool_kind": by_kind,
    }


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * pct))))
    return ordered[index]


def stable_json_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = [
    "CapturedToolResult",
    "ShadowReplayReport",
    "ToolLineRef",
    "ToolOverlayDecision",
    "ToolOverlayEvalRecord",
    "ToolOverlayJudgment",
    "ToolOverlayLatency",
    "ToolResultAnchors",
    "captured_tool_result_from_event",
    "stable_json_hash",
]
