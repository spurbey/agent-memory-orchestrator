from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


ANSWER_GRADE_KINDS = frozenset(
    {
        "Decision",
        "DecisionUnit",
        "Fix",
        "Bug",
        "Blocker",
        "OpenQuestion",
        "TestRun",
        "CodeNode",
        "CodeHunk",
    }
)

SUPPORT_ONLY_KINDS = frozenset(
    {
        "RawEvidenceRef",
        "Prompt",
        "ToolUse",
        "ToolResult",
        "CleanedEvidenceWindow",
        "GraphDelta",
        "Session",
        "Repo",
        "Branch",
        "File",
        "Community",
    }
)

VALID_EXTRACTION_RUN_STATUSES = frozenset({"draft", "partial", "failed", "complete", "selected", "finalized"})
VALID_GRAPH_STATUSES = frozenset(
    {
        "draft",
        "session_final",
        "active",
        "committed",
        "refined",
        "superseded",
        "contested",
        "contested_pending_review",
        "abandoned",
    }
)


@dataclass(slots=True, frozen=True)
class ExtractionRun:
    id: str
    session_id: str
    evidence_ids: tuple[str, ...]
    transcript_paths: tuple[str, ...] = ()
    algorithm_versions: dict[str, str] = field(default_factory=dict)
    model_versions: dict[str, str] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)
    status: str = "draft"
    diagnostics: tuple[str, ...] = ()
    created_at: str = ""

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        evidence_ids: list[str] | tuple[str, ...],
        transcript_paths: list[str] | tuple[str, ...] = (),
        run_id: str = "",
    ) -> "ExtractionRun":
        safe_session = str(session_id).strip()
        safe_ids = tuple(str(item).strip() for item in evidence_ids if str(item).strip())
        resolved_id = run_id or f"extraction_run:{safe_session}:{safe_ids[0] if safe_ids else 'empty'}"
        return cls(id=resolved_id, session_id=safe_session, evidence_ids=safe_ids, transcript_paths=tuple(transcript_paths))

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class TimelineEvent:
    id: str
    session_id: str
    event_type: str
    timestamp: str
    source_app: str = "codex"
    evidence_id: str = ""
    transcript_path: str = ""
    content: str = ""
    tool_name: str = ""
    files: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_raw_evidence(cls, raw: dict[str, Any]) -> "TimelineEvent":
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        event_type = str(raw.get("event_name") or payload.get("hook_event_name") or raw.get("event_type") or "").strip()
        evidence_id = str(raw.get("id") or raw.get("evidence_id") or "").strip()
        session_id = str(raw.get("session_id") or payload.get("session_id") or "").strip()
        timestamp = str(raw.get("created_at") or payload.get("timestamp") or "").strip()
        tool_name = str(payload.get("tool_name") or raw.get("tool_name") or "").strip()
        content = _event_content(raw, payload)
        transcript_path = str(payload.get("transcript_path") or raw.get("transcript_path") or "").strip()
        files = tuple(_extract_files(raw, payload))
        return cls(
            id=f"event:{evidence_id}" if evidence_id else f"event:{session_id}:{timestamp}:{event_type}",
            session_id=session_id,
            event_type=_snake(event_type),
            timestamp=timestamp,
            source_app=str(raw.get("source_app") or payload.get("source_app") or "codex"),
            evidence_id=evidence_id,
            transcript_path=transcript_path,
            content=content,
            tool_name=tool_name,
            files=files,
            metadata={"raw_event_name": event_type},
        )

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class DecisionThread:
    id: str
    session_id: str
    extraction_run_id: str
    event_ids: tuple[str, ...]
    topic: str
    file_paths: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    kind: str = "DecisionThread"
    source: str = "deterministic"
    confidence: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class DecisionUnit:
    id: str
    session_id: str
    extraction_run_id: str
    summary: str
    evidence_ids: tuple[str, ...]
    kind: str = "Decision"
    confidence: float = 0.6
    source: str = "deterministic"
    qwen_call: str = ""
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class CodeHunk:
    id: str
    session_id: str
    extraction_run_id: str
    file_path: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    patch: str
    commit_id: str
    evidence_ids: tuple[str, ...]
    kind: str = "CodeHunk"
    source: str = "deterministic"
    confidence: float = 1.0
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class CodeNode:
    id: str
    session_id: str
    extraction_run_id: str
    file_path: str
    ast_type: str
    line_start: int
    line_end: int
    content: str
    commit_id: str
    evidence_ids: tuple[str, ...]
    prev_content: str = ""
    ast_status: str = "parsed"
    kind: str = "CodeNode"
    source: str = "deterministic"
    confidence: float = 1.0
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class TestRun:
    id: str
    session_id: str
    extraction_run_id: str
    command: str
    result: str
    evidence_ids: tuple[str, ...]
    kind: str = "TestRun"
    source: str = "deterministic"
    confidence: float = 1.0
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


@dataclass(slots=True, frozen=True)
class MergePlan:
    id: str
    session_id: str
    extraction_run_id: str
    commit_id: str
    planned_promotions: tuple[str, ...] = ()
    planned_edges: tuple[dict[str, Any], ...] = ()
    review_candidates: tuple[dict[str, Any], ...] = ()
    status: str = "draft"
    metadata: dict[str, Any] = field(default_factory=dict)

    kind: str = "MergePlan"
    source: str = "deterministic"
    confidence: float = 1.0
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return _asdict(self)


def _asdict(obj: Any) -> dict[str, Any]:
    return {field_name: getattr(obj, field_name) for field_name in obj.__dataclass_fields__}  # type: ignore[attr-defined]


def _event_content(raw: dict[str, Any], payload: dict[str, Any]) -> str:
    for key in ("prompt", "message", "content", "tool_response", "last_assistant_message"):
        value = payload.get(key, raw.get(key))
        if isinstance(value, str) and value.strip():
            return value.strip()
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command.strip()
    return ""


def _extract_files(raw: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    files: list[str] = []
    for container in (payload, raw):
        value = container.get("files") or container.get("changed_files")
        if isinstance(value, list):
            files.extend(str(item).strip() for item in value if str(item).strip())
    response = payload.get("tool_response")
    if isinstance(response, str):
        for line in response.replace("\\n", "\n").splitlines():
            line = line.strip()
            if line.startswith(("M ", "A ")):
                files.append(line[2:].strip())
    return _dedupe(files)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _snake(value: str) -> str:
    out = []
    last_lower = False
    for ch in value.strip():
        if ch.isupper() and last_lower:
            out.append("_")
        if ch.isalnum():
            out.append(ch.lower())
            last_lower = ch.islower() or ch.isdigit()
        else:
            if out and out[-1] != "_":
                out.append("_")
            last_lower = False
    return "".join(out).strip("_")
