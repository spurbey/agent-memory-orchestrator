from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .models import TimelineEvent


@dataclass(slots=True, frozen=True)
class TimelineEdge:
    id: str
    source_id: str
    target_id: str
    kind: str = "FOLLOWED_BY"
    weight: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "target_id": self.target_id,
            "kind": self.kind,
            "weight": self.weight,
            "metadata": self.metadata,
        }


@dataclass(slots=True, frozen=True)
class TimelineGraph:
    session_id: str
    events: tuple[TimelineEvent, ...]
    edges: tuple[TimelineEdge, ...]
    diagnostics: tuple[str, ...] = ()

    def event_types(self) -> set[str]:
        return {event.event_type for event in self.events}

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "events": [event.as_dict() for event in self.events],
            "edges": [edge.as_dict() for edge in self.edges],
            "diagnostics": list(self.diagnostics),
        }


def load_amo_evidence_events(evidence_paths: Iterable[Path], *, session_id: str) -> list[TimelineEvent]:
    events: list[TimelineEvent] = []
    for path in evidence_paths:
        if not path.exists():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                raw = _loads_jsonl(line)
                if raw is None:
                    continue
                payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
                raw_session_id = str(raw.get("session_id") or payload.get("session_id") or "")
                if raw_session_id != session_id:
                    continue
                events.append(TimelineEvent.from_raw_evidence(raw))
    return events


def load_codex_transcript_events(transcript_path: Path, *, session_id: str) -> list[TimelineEvent]:
    if not transcript_path.exists():
        return []
    events: list[TimelineEvent] = []
    with transcript_path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw = _loads_jsonl(line)
            if raw is None:
                continue
            event = _transcript_event(raw, session_id=session_id, transcript_path=transcript_path, line_no=line_no)
            if event is not None:
                events.append(event)
    return events


def build_timeline(
    *,
    session_id: str,
    evidence_paths: Iterable[Path] = (),
    transcript_paths: Iterable[Path] = (),
) -> TimelineGraph:
    diagnostics: list[str] = []
    events: list[TimelineEvent] = []
    events.extend(load_amo_evidence_events(evidence_paths, session_id=session_id))
    for transcript_path in transcript_paths:
        transcript_events = load_codex_transcript_events(transcript_path, session_id=session_id)
        if not transcript_events:
            diagnostics.append(f"no_transcript_events:{transcript_path}")
        events.extend(transcript_events)
    ordered = _dedupe_events(events)
    ordered.sort(key=lambda event: (event.timestamp, event.id))
    edges = tuple(
        TimelineEdge(
            id=f"edge:{source.id}:FOLLOWED_BY:{target.id}",
            source_id=source.id,
            target_id=target.id,
            metadata={"session_id": session_id},
        )
        for source, target in zip(ordered, ordered[1:])
    )
    return TimelineGraph(session_id=session_id, events=tuple(ordered), edges=edges, diagnostics=tuple(diagnostics))


def _transcript_event(
    raw: dict[str, Any],
    *,
    session_id: str,
    transcript_path: Path,
    line_no: int,
) -> TimelineEvent | None:
    timestamp = str(raw.get("timestamp") or "")
    raw_type = str(raw.get("type") or "")
    payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
    payload_type = str(payload.get("type") or "")
    base = {
        "session_id": session_id,
        "timestamp": timestamp,
        "source_app": "codex_transcript",
        "transcript_path": str(transcript_path),
        "metadata": {"line_no": line_no, "raw_type": raw_type, "payload_type": payload_type},
    }

    if raw_type == "session_meta":
        payload_session_id = str(payload.get("id") or "")
        if payload_session_id != session_id:
            return None
        return TimelineEvent(
            id=f"transcript:{session_id}:session_start:{line_no}",
            event_type="session_start",
            content=str(payload.get("cwd") or ""),
            **base,
        )

    if raw_type == "response_item" and payload_type == "message":
        role = str(payload.get("role") or "")
        if role not in {"user", "assistant"}:
            return None
        content = _message_content(payload)
        if not content:
            return None
        return TimelineEvent(
            id=f"transcript:{session_id}:{role}:{line_no}",
            event_type="agent_message" if role == "assistant" else "user_message",
            content=content,
            **base,
        )

    if raw_type == "response_item" and payload_type in {"function_call", "custom_tool_call"}:
        call_id = str(payload.get("call_id") or f"line-{line_no}")
        tool_name = str(payload.get("name") or "")
        content = str(payload.get("arguments") or payload.get("input") or "")
        return TimelineEvent(
            id=f"transcript:{session_id}:tool_use:{call_id}",
            event_type="tool_use",
            content=content,
            tool_name=tool_name,
            files=tuple(_extract_files_from_text(content)),
            metadata={**base["metadata"], "call_id": call_id},
            **{key: value for key, value in base.items() if key != "metadata"},
        )

    if raw_type == "response_item" and payload_type in {"function_call_output", "custom_tool_call_output"}:
        call_id = str(payload.get("call_id") or f"line-{line_no}")
        content = str(payload.get("output") or "")
        return TimelineEvent(
            id=f"transcript:{session_id}:tool_result:{call_id}",
            event_type="tool_result",
            content=content,
            files=tuple(_extract_files_from_text(content)),
            metadata={**base["metadata"], "call_id": call_id},
            **{key: value for key, value in base.items() if key != "metadata"},
        )

    if raw_type == "event_msg" and payload_type in {"task_complete", "turn_aborted"}:
        return TimelineEvent(
            id=f"transcript:{session_id}:stop:{line_no}",
            event_type="stop",
            content=payload_type,
            **base,
        )

    return None


def _dedupe_events(events: list[TimelineEvent]) -> list[TimelineEvent]:
    out: list[TimelineEvent] = []
    seen: set[tuple[str, str]] = set()
    for event in events:
        key = _dedupe_key(event)
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    return out


def _dedupe_key(event: TimelineEvent) -> tuple[str, str]:
    if event.evidence_id:
        return ("evidence", event.evidence_id)
    call_id = str(event.metadata.get("call_id") or "")
    if call_id:
        return (event.event_type, call_id)
    content_fingerprint = event.content.strip()[:240]
    return (event.event_type, f"{event.timestamp}:{content_fingerprint}")


def _message_content(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            value = item.get("text") or item.get("content")
            if isinstance(value, str):
                parts.append(value)
        return "\n".join(part.strip() for part in parts if part.strip())
    return ""


def _extract_files_from_text(text: str) -> list[str]:
    files: list[str] = []
    normalized = text.replace("\\n", "\n")
    for line in normalized.splitlines():
        stripped = line.strip().strip('"')
        if stripped.startswith(("M ", "A ")):
            files.append(stripped[2:].strip())
        if stripped.startswith("*** Update File: "):
            files.append(stripped.removeprefix("*** Update File: ").strip())
        if stripped.startswith("*** Add File: "):
            files.append(stripped.removeprefix("*** Add File: ").strip())
    return _dedupe_strings(files)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _loads_jsonl(line: str) -> dict[str, Any] | None:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None
