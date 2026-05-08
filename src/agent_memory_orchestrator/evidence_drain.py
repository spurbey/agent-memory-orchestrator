from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .graph_store import GraphStore
from .graph_triggers import detect_trigger
from .session_graph import SessionGraphBuilder
from .versioning import VersionBackend


@dataclass(slots=True)
class DrainSessionState:
    pending_records: list[dict[str, Any]] = field(default_factory=list)
    pending_write: bool = False
    processed_windows: int = 0


class EvidenceDrain:
    """Drains append-only hook evidence into the Kuzu session graph.

    This class is daemon-side. Hooks must not instantiate it.
    """

    def __init__(
        self,
        settings: Settings,
        store: GraphStore,
        version_backend: VersionBackend,
        *,
        cursor_path: Path | None = None,
        evidence_roots: list[Path] | None = None,
        builder: SessionGraphBuilder | None = None,
    ) -> None:
        self.settings = settings
        self.cursor_path = cursor_path or settings.home / ".state" / "evidence_cursors.json"
        self.evidence_roots = evidence_roots or [settings.evidence_dir]
        self.builder = builder or SessionGraphBuilder(settings, store, version_backend)

    def drain(self, *, limit: int = 500, session_id: str = "") -> dict[str, Any]:
        start = time.monotonic()
        cursors = self._load_cursors()
        states: dict[str, DrainSessionState] = {}
        stats: dict[str, Any] = {
            "ok": True,
            "records_seen": 0,
            "records_ingested": 0,
            "records_skipped": 0,
            "windows_processed": 0,
            "triggered": [],
            "skipped": [],
            "cursor_path": str(self.cursor_path),
        }
        for path in self._evidence_files():
            key = str(path.resolve())
            offset = int(cursors.get(key, 0))
            for next_offset, record in _read_jsonl_from(path, offset):
                cursors[key] = next_offset
                stats["records_seen"] += 1
                if session_id and str(record.get("session_id") or "") != session_id:
                    stats["records_skipped"] += 1
                    continue
                current_session = str(record.get("session_id") or "default")
                state = states.setdefault(current_session, DrainSessionState())
                ingest = self.builder.ingest_basic_record(record)
                stats["records_ingested"] += 1
                decision = detect_trigger(record, pending_write=state.pending_write)
                if decision.is_write:
                    state.pending_write = True
                state.pending_records.append(record)
                if decision.should_process:
                    result = self.builder.process_window(
                        session_id=current_session,
                        records=state.pending_records,
                        trigger=decision,
                    )
                    state.processed_windows += 1
                    state.pending_records = []
                    state.pending_write = False
                    stats["windows_processed"] += 1
                    stats["triggered"].append(
                        {
                            "session_id": current_session,
                            "trigger": decision.as_dict(),
                            "result": _compact_result(result),
                            "latest_event": ingest,
                        }
                    )
                else:
                    stats["skipped"].append(
                        {
                            "session_id": current_session,
                            "evidence_id": record.get("id"),
                            "decision": decision.as_dict(),
                        }
                    )
                if stats["records_seen"] >= max(1, int(limit)):
                    self._save_cursors(cursors)
                    stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
                    return stats
        self._save_cursors(cursors)
        stats["elapsed_ms"] = int((time.monotonic() - start) * 1000)
        return stats

    def pending(self, *, session_id: str = "") -> dict[str, Any]:
        cursors = self._load_cursors()
        rows: list[dict[str, Any]] = []
        for path in self._evidence_files():
            key = str(path.resolve())
            offset = int(cursors.get(key, 0))
            for next_offset, record in _read_jsonl_from(path, offset):
                if session_id and str(record.get("session_id") or "") != session_id:
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


def _read_jsonl_from(path: Path, offset: int) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
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
            except (UnicodeDecodeError, json.JSONDecodeError):
                rows.append((next_offset, {"id": "", "hash": "", "offset": start, "path": str(path), "payload": {}}))
                continue
            if isinstance(payload, dict):
                payload.setdefault("path", str(path.resolve()))
                payload.setdefault("offset", start)
                rows.append((next_offset, payload))
    return rows


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "processed": bool(result.get("processed")),
        "context_node_id": result.get("context_node_id"),
        "work_node_id": result.get("work_node_id"),
        "node_count": len(result.get("nodes", [])) if isinstance(result.get("nodes"), list) else 0,
        "evidence_ids": result.get("evidence_ids", []),
    }
