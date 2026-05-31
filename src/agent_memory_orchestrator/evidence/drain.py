from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..reasoning_graph.jobs import ProductionSessionJobStore
from .triggers import detect_trigger
from .triggers import is_session_start
from .triggers import record_session_id
from .triggers import session_boundary_trigger


@dataclass(slots=True)
class DrainSessionState:
    pending_count: int = 0
    first_event_id: str = ""
    latest_event_id: str = ""
    source_app: str = ""
    repo_path: str = ""
    evidence_days: set[str] = field(default_factory=set)
    enqueued_windows: int = 0
    pending_records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True, frozen=True)
class JsonlReadIssue:
    path: str
    offset: int
    next_offset: int
    error_type: str
    error: str
    raw_sha256: str
    preview: str
    quarantine_path: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "offset": self.offset,
            "next_offset": self.next_offset,
            "error_type": self.error_type,
            "error": self.error,
            "raw_sha256": self.raw_sha256,
            "preview": self.preview,
            "quarantine_path": self.quarantine_path,
        }


class EvidenceDrain:
    """Drains append-only hook evidence into production session jobs.

    This class is daemon-side. Hooks must not instantiate it.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        cursor_path: Path | None = None,
        pending_path: Path | None = None,
        evidence_roots: list[Path] | None = None,
        job_store: ProductionSessionJobStore | None = None,
    ) -> None:
        self.settings = settings
        self.cursor_path = cursor_path or settings.home / ".state" / "evidence_cursors.json"
        self.pending_path = pending_path or settings.home / ".state" / "evidence_pending_windows.json"
        self.evidence_roots = evidence_roots or [settings.evidence_dir]
        self.job_store = job_store if job_store is not None else ProductionSessionJobStore(settings)

    def drain(self, *, limit: int = 500, session_id: str = "", max_windows: int | None = None) -> dict[str, Any]:
        start = time.monotonic()
        safe_max_windows = max(1, int(max_windows or self.settings.drain_max_windows_per_run))
        cursors = self._load_cursors()
        session_filter = str(session_id or "")
        states, last_active_session_id = self._load_pending_states()
        stats: dict[str, Any] = {
            "ok": True,
            "records_seen": 0,
            "records_ingested": 0,
            "records_skipped": 0,
            "malformed_records": 0,
            "malformed": [],
            "windows_processed": 0,
            "triggered": [],
            "skipped": [],
            "cursor_path": str(self.cursor_path),
            "pending_path": str(self.pending_path),
            "processing_trigger": "session_boundary",
        }
        for path in self._evidence_files():
            key = _cursor_key(path, session_filter)
            offset = int(cursors.get(key, 0))
            evidence_day = path.stem
            for next_offset, record, issue in _iter_jsonl_from(
                path,
                offset,
                quarantine_dir=self.settings.home / ".state" / "malformed_evidence",
            ):
                cursors[key] = next_offset
                if issue is not None:
                    stats["malformed_records"] += 1
                    if len(stats["malformed"]) < 20:
                        stats["malformed"].append(issue.as_dict())
                    continue
                if record is None:
                    continue
                stats["records_seen"] += 1

                current_session = record_session_id(record)
                boundary_processed = False
                if is_session_start(record):
                    previous_session = last_active_session_id
                    if previous_session and previous_session != current_session:
                        previous_state = states.get(previous_session)
                        if (
                            previous_state is not None
                            and previous_state.pending_count
                            and (not session_filter or previous_session == session_filter)
                        ):
                            decision = session_boundary_trigger(previous_session, current_session)
                            result = self._process_or_enqueue_closed_session(
                                session_id=previous_session,
                                new_session_id=current_session,
                                boundary_event_id=str(record.get("id") or ""),
                                state=previous_state,
                                trigger=decision,
                            )
                            previous_state.enqueued_windows += 1
                            previous_state.pending_count = 0
                            previous_state.pending_records = []
                            stats["windows_processed"] += 1
                            stats["triggered"].append(
                                {
                                    "session_id": previous_session,
                                    "trigger": decision.as_dict(),
                                    "result": _compact_result(result),
                                    "latest_event": {
                                        "boundary_event_id": record.get("id"),
                                        "new_session_id": current_session,
                                    },
                                }
                            )
                            boundary_processed = True
                    last_active_session_id = current_session
                elif not last_active_session_id:
                    last_active_session_id = current_session

                if session_filter and current_session != session_filter:
                    stats["records_skipped"] += 1
                    if boundary_processed and stats["windows_processed"] >= safe_max_windows:
                        self._save_state(cursors, states, last_active_session_id)
                        stats["stopped_reason"] = "max_windows_reached"
                        stats["max_windows"] = safe_max_windows
                        stats["pending_sessions"] = _pending_session_count(states)
                        stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
                        return stats
                    continue

                state = states.setdefault(current_session, DrainSessionState())
                stats["records_ingested"] += 1
                decision = detect_trigger(record)
                _update_state_from_record(state, record, evidence_day=evidence_day)
                stats["skipped"].append(
                    {
                        "session_id": current_session,
                        "evidence_id": record.get("id"),
                        "decision": decision.as_dict(),
                    }
                )
                if boundary_processed and stats["windows_processed"] >= safe_max_windows:
                    self._save_state(cursors, states, last_active_session_id)
                    stats["stopped_reason"] = "max_windows_reached"
                    stats["max_windows"] = safe_max_windows
                    stats["pending_sessions"] = _pending_session_count(states)
                    stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
                    return stats
                if stats["records_ingested"] >= max(1, int(limit)):
                    self._save_state(cursors, states, last_active_session_id)
                    stats["stopped_reason"] = "record_limit_reached"
                    stats["max_windows"] = safe_max_windows
                    stats["pending_sessions"] = _pending_session_count(states)
                    stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
                    return stats
        self._save_state(cursors, states, last_active_session_id)
        stats["stopped_reason"] = "evidence_exhausted"
        stats["max_windows"] = safe_max_windows
        stats["pending_sessions"] = _pending_session_count(states)
        stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        return stats

    def pending(self, *, session_id: str = "") -> dict[str, Any]:
        cursors = self._load_cursors()
        session_filter = str(session_id or "")
        rows: list[dict[str, Any]] = []
        for path in self._evidence_files():
            key = _cursor_key(path, session_filter)
            offset = int(cursors.get(key, 0))
            for next_offset, record in _read_jsonl_from(path, offset):
                if session_filter and str(record.get("session_id") or "") != session_filter:
                    continue
                rows.append(
                    {
                        "path": key,
                        "offset": offset,
                        "next_offset": next_offset,
                        "id": record.get("id"),
                        "session_id": record.get("session_id"),
                        "event_name": record.get("event_name"),
                    }
                )
        return {"ok": True, "count": len(rows), "pending": rows[:100], "cursor_path": str(self.cursor_path)}

    def _evidence_files(self) -> list[Path]:
        files: list[Path] = []
        for root in self.evidence_roots:
            if root.exists():
                files.extend(sorted(root.glob("*.jsonl")))
        return files

    def _load_cursors(self) -> dict[str, int]:
        if not self.cursor_path.exists():
            return {}
        try:
            payload = json.loads(self.cursor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key): int(value) for key, value in payload.items()}

    def _save_cursors(self, cursors: dict[str, int]) -> None:
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self.cursor_path.write_text(json.dumps(cursors, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _load_pending_states(self) -> tuple[dict[str, DrainSessionState], str]:
        if not self.pending_path.exists():
            return {}, ""
        try:
            payload = json.loads(self.pending_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}, ""
        sessions = payload.get("sessions") if isinstance(payload, dict) else {}
        if not isinstance(sessions, dict):
            return {}, ""
        states: dict[str, DrainSessionState] = {}
        for session_id, row in sessions.items():
            if not isinstance(row, dict):
                continue
            records = row.get("pending_records")
            days = row.get("evidence_days")
            states[str(session_id)] = DrainSessionState(
                pending_count=int(row.get("pending_count", len(records) if isinstance(records, list) else 0)),
                first_event_id=str(row.get("first_event_id") or ""),
                latest_event_id=str(row.get("latest_event_id") or ""),
                source_app=str(row.get("source_app") or ""),
                repo_path=str(row.get("repo_path") or ""),
                evidence_days=set(str(item) for item in days if str(item)) if isinstance(days, list) else set(),
                enqueued_windows=int(row.get("enqueued_windows", row.get("processed_windows", 0))),
            )
        last_active_session_id = str(payload.get("last_active_session_id") or "") if isinstance(payload, dict) else ""
        return states, last_active_session_id

    def _save_pending_states(self, states: dict[str, DrainSessionState], last_active_session_id: str) -> None:
        sessions = {
            session_id: {
                "pending_count": state.pending_count,
                "first_event_id": state.first_event_id,
                "latest_event_id": state.latest_event_id,
                "source_app": state.source_app,
                "repo_path": state.repo_path,
                "evidence_days": sorted(state.evidence_days),
                "enqueued_windows": state.enqueued_windows,
            }
            for session_id, state in sorted(states.items())
            if state.pending_count
        }
        self.pending_path.parent.mkdir(parents=True, exist_ok=True)
        self.pending_path.write_text(
            json.dumps(
                {
                    "last_active_session_id": last_active_session_id,
                    "sessions": sessions,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _save_state(
        self,
        cursors: dict[str, int],
        states: dict[str, DrainSessionState],
        last_active_session_id: str,
    ) -> None:
        self._save_cursors(cursors)
        self._save_pending_states(states, last_active_session_id)

    def _process_or_enqueue_closed_session(
        self,
        *,
        session_id: str,
        new_session_id: str,
        boundary_event_id: str,
        state: DrainSessionState,
        trigger: Any,
    ) -> dict[str, Any]:
        del trigger
        enqueue = self.job_store.enqueue_session(
            session_id=session_id,
            boundary_event_id=boundary_event_id,
            source_app=state.source_app,
            repo_path=state.repo_path,
            source_evidence_day=sorted(state.evidence_days)[-1] if state.evidence_days else "",
            source_evidence_days=sorted(state.evidence_days),
        )
        return {
            "mode": "production_job_enqueue",
            "job_id": enqueue.job.get("job_id"),
            "created": enqueue.created,
            "updated": enqueue.updated,
            "reason": enqueue.reason,
            "closed_by_session_id": new_session_id,
        }


def _read_jsonl_from(path: Path, offset: int) -> list[tuple[int, dict[str, Any]]]:
    """Read valid JSONL rows from an offset.

    Malformed rows are skipped by design. Drain callers use `_iter_jsonl_from`
    directly so they can advance cursors and quarantine bad lines.
    """

    return [
        (next_offset, record)
        for next_offset, record, issue in _iter_jsonl_from(path, offset)
        if issue is None and record is not None
    ]


def _iter_jsonl_from(
    path: Path,
    offset: int,
    *,
    quarantine_dir: Path | None = None,
) -> list[tuple[int, dict[str, Any] | None, JsonlReadIssue | None]]:
    rows: list[tuple[int, dict[str, Any] | None, JsonlReadIssue | None]] = []
    with path.open("rb") as handle:
        handle.seek(offset)
        while True:
            start = handle.tell()
            line = handle.readline()
            if not line:
                break
            next_offset = handle.tell()
            try:
                payload = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                issue = _jsonl_issue(
                    path=path,
                    offset=start,
                    next_offset=next_offset,
                    raw=line,
                    exc=exc,
                    quarantine_dir=quarantine_dir,
                )
                rows.append((next_offset, None, issue))
                continue
            if not isinstance(payload, dict):
                issue = _jsonl_issue(
                    path=path,
                    offset=start,
                    next_offset=next_offset,
                    raw=line,
                    exc=TypeError(f"JSONL row must be an object, got {type(payload).__name__}"),
                    quarantine_dir=quarantine_dir,
                )
                rows.append((next_offset, None, issue))
                continue
            payload.setdefault("path", str(path.resolve()))
            payload.setdefault("offset", start)
            rows.append((next_offset, payload, None))
    return rows


def _jsonl_issue(
    *,
    path: Path,
    offset: int,
    next_offset: int,
    raw: bytes,
    exc: BaseException,
    quarantine_dir: Path | None,
) -> JsonlReadIssue:
    digest = hashlib.sha256(raw).hexdigest()
    decoded = raw.decode("utf-8", errors="replace")
    preview = decoded[:500].replace("\n", "\\n").replace("\r", "\\r")
    quarantine_path = ""
    issue = JsonlReadIssue(
        path=str(path.resolve()),
        offset=offset,
        next_offset=next_offset,
        error_type=type(exc).__name__,
        error=str(exc),
        raw_sha256=digest,
        preview=preview,
    )
    if quarantine_dir is not None:
        quarantine_path = _quarantine_jsonl_issue(quarantine_dir, path, raw, issue)
        issue = JsonlReadIssue(
            path=issue.path,
            offset=issue.offset,
            next_offset=issue.next_offset,
            error_type=issue.error_type,
            error=issue.error,
            raw_sha256=issue.raw_sha256,
            preview=issue.preview,
            quarantine_path=quarantine_path,
        )
    return issue


def _quarantine_jsonl_issue(quarantine_dir: Path, source_path: Path, raw: bytes, issue: JsonlReadIssue) -> str:
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    target = quarantine_dir / f"{source_path.name}.bad.jsonl"
    row = {
        **issue.as_dict(),
        "source_file": str(source_path.resolve()),
        "raw_line": raw.decode("utf-8", errors="replace"),
    }
    with target.open("ab") as handle:
        handle.write((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    return str(target.resolve())


def _cursor_key(path: Path, session_id: str = "") -> str:
    resolved = str(path.resolve())
    if session_id:
        return f"{resolved}::session::{session_id}"
    return resolved


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": result.get("mode", ""),
        "job_id": result.get("job_id", ""),
        "created": result.get("created"),
        "updated": result.get("updated"),
        "reason": result.get("reason", ""),
        "processed": bool(result.get("processed")),
        "context_node_id": result.get("context_node_id"),
        "work_node_id": result.get("work_node_id"),
        "node_count": len(result.get("nodes", [])) if isinstance(result.get("nodes"), list) else 0,
        "evidence_ids": result.get("evidence_ids", []),
    }


def _pending_session_count(states: dict[str, DrainSessionState]) -> int:
    return sum(1 for state in states.values() if state.pending_count)


def _update_state_from_record(state: DrainSessionState, record: dict[str, Any], *, evidence_day: str) -> None:
    event_id = str(record.get("id") or "")
    state.pending_count += 1
    state.pending_records.append(record)
    if event_id and not state.first_event_id:
        state.first_event_id = event_id
    if event_id:
        state.latest_event_id = event_id
    source_app = str(record.get("source_app") or "")
    if source_app and not state.source_app:
        state.source_app = source_app
    repo_path = _record_repo_path(record)
    if repo_path and not state.repo_path:
        state.repo_path = repo_path
    if evidence_day:
        state.evidence_days.add(evidence_day)


def _record_repo_path(record: dict[str, Any]) -> str:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    for key in ("cwd", "repo_path", "repo_root", "workspace", "workspace_root"):
        value = payload.get(key, record.get(key))
        if isinstance(value, str) and value.strip():
            return value.strip()
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("cwd", "repo_path", "repo_root"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""

