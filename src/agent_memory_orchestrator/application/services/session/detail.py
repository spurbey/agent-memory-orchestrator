from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....domain.evidence import TriggerDecision
from ....domain.evidence import clean_evidence_window
from ....domain.evidence import is_session_start
from ....domain.evidence import record_session_id
from ....domain.evidence import session_boundary_trigger
from ....domain.retrieval.text import clip_text as _clip
from ....evidence.drain import _read_jsonl_from
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore


def _load_evidence_records(
    roots: list[Path],
    *,
    session_id: str = "",
    limit: int = 500,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*.jsonl")):
            for _next_offset, record in _read_jsonl_from(path, 0):
                if session_id and str(record.get("session_id") or "") != session_id:
                    continue
                records.append(record)
    records.sort(key=lambda record: str(record.get("created_at") or ""))
    return records[-max(1, int(limit)) :]


def build_session_detail_fallback(
    settings: Settings,
    *,
    session_id: str,
    limit: int = 120,
    error: Exception | None = None,
) -> dict[str, Any]:
    """Return selected-session detail without opening Kuzu.

    This is the dashboard fallback path when the graph file is temporarily
    unavailable. It still shows immutable raw production artifacts so the operator can
    inspect the session while graph reads recover.
    """

    safe_session_id = str(session_id or "").strip()
    if not safe_session_id:
        raise ValueError("session_id is required")
    safe_limit = max(1, min(500, int(limit)))
    records, evidence_source = _load_session_evidence_records(settings, session_id=safe_session_id, limit=safe_limit)
    pending = _session_pending_summary(settings, session_id=safe_session_id)
    graph_warning = "graph_temporarily_unavailable" if error is not None else "graph_not_loaded_for_fast_session_detail"
    warning = {
        "ok": False,
        "error": str(error or graph_warning),
        "error_type": type(error).__name__ if error is not None else "GraphUnavailable",
        "warning": graph_warning,
    }
    return {
        "ok": True,
        "degraded": error is not None,
        "mode": "artifact_only",
        "session_id": safe_session_id,
        "timeline": [_timeline_row(record) for record in records],
        "windows": _reconstruct_clean_windows(records, []),
        "current_context": {"ok": True, "nodes": [], "source": "not_loaded_graph_unavailable"},
        "merge_status": warning,
        "merge_preview": warning,
        "pending": {"count": pending.get("count", 0), "cursor_path": pending.get("cursor_path"), "source": pending.get("source")},
        "evidence_source": evidence_source,
        "graph": {"nodes": [], "edges": [], "warning": graph_warning},
        "central_graph": {"ok": False, "nodes": [], "edges": [], "warnings": [graph_warning], "status": warning},
    }


def _load_session_evidence_records(settings: Settings, *, session_id: str, limit: int = 500) -> tuple[list[dict[str, Any]], str]:
    artifact_records = _load_production_session_raw_evidence_artifact(settings, session_id=session_id, limit=limit)
    if artifact_records is not None:
        return artifact_records, "production_session_raw_evidence_artifact"
    return _load_evidence_records(_evidence_roots(settings), session_id=session_id, limit=limit), "raw_evidence_scan"


def _load_production_session_raw_evidence_artifact(settings: Settings, *, session_id: str, limit: int = 500) -> list[dict[str, Any]] | None:
    job_store = ProductionSessionJobStore(settings)
    try:
        job = job_store.get_job_by_session(session_id=session_id)
        if not job:
            return None
        stage = job_store.stage_row(job_id=str(job.get("job_id") or ""), stage="evidence_view")
        if not stage:
            return None
    finally:
        job_store.close()
    view_path = Path(str(stage.get("output_artifact") or ""))
    if not view_path.exists():
        return None
    try:
        view = json.loads(view_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_path = Path(str(view.get("input_raw") or ""))
    if not raw_path.exists() or raw_path.is_dir():
        return None
    records = [record for _next_offset, record in _read_jsonl_from(raw_path, 0)]
    records.sort(key=lambda record: str(record.get("created_at") or ""))
    return records[-max(1, int(limit)) :]


def _session_pending_summary(settings: Settings, *, session_id: str) -> dict[str, Any]:
    job_store = ProductionSessionJobStore(settings)
    try:
        job = job_store.get_job_by_session(session_id=session_id)
    finally:
        job_store.close()
    if job:
        return {
            "ok": True,
            "count": 0,
            "pending": [],
            "cursor_path": "",
            "source": "production_job_state",
            "job_status": job.get("status"),
            "current_stage": job.get("current_stage"),
        }
    return {
        "ok": True,
        "count": 0,
        "pending": [],
        "cursor_path": "",
        "source": "not_loaded_no_production_job",
    }


def _timeline_row(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or tool_input.get("cmd") or "")
    text = (
        str(payload.get("prompt") or "")
        or str(payload.get("tool_response") or "")
        or str(payload.get("last_assistant_message") or "")
        or command
    )
    return {
        "id": record.get("id"),
        "created_at": record.get("created_at"),
        "event_name": record.get("event_name"),
        "source_app": record.get("source_app"),
        "tool": payload.get("tool") or payload.get("tool_name") or "",
        "command": _clip(command, 260),
        "summary": _clip(text, 420),
        "payload_keys": sorted(payload.keys()),
        "raw_ref": {
            "path": record.get("path"),
            "offset": record.get("offset"),
            "hash": record.get("hash"),
        },
    }


def _reconstruct_clean_windows(records: list[dict[str, Any]], nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending_by_session: dict[str, list[dict[str, Any]]] = {}
    last_active_session_id = ""
    windows: list[dict[str, Any]] = []
    for record in records:
        current_session = record_session_id(record)
        if is_session_start(record):
            if last_active_session_id and last_active_session_id != current_session:
                pending = pending_by_session.get(last_active_session_id, [])
                if pending:
                    decision = session_boundary_trigger(last_active_session_id, current_session)
                    windows.append(_window_row(len(windows) + 1, pending, decision, nodes))
                    pending_by_session[last_active_session_id] = []
            last_active_session_id = current_session
        elif not last_active_session_id:
            last_active_session_id = current_session
        pending_by_session.setdefault(current_session, []).append(record)

    for pending_records in pending_by_session.values():
        if not pending_records:
            continue
        preview_trigger = TriggerDecision(False, "pending", "pending raw evidence window")
        windows.append(
            {
                "index": len(windows) + 1,
                "status": "pending",
                "trigger": preview_trigger.as_dict(),
                "evidence_ids": [str(record.get("id") or "") for record in pending_records if record.get("id")],
                "cleaned_evidence": clean_evidence_window(pending_records, preview_trigger),
                "graph_nodes": [],
                "graph_edges": [],
            }
        )
    return windows


def _window_row(
    index: int,
    records: list[dict[str, Any]],
    trigger: TriggerDecision,
    nodes: list[dict[str, Any]],
) -> dict[str, Any]:
    evidence_ids = [str(record.get("id") or "") for record in records if record.get("id")]
    graph_nodes = _nodes_for_evidence(nodes, evidence_ids)
    return {
        "index": index,
        "status": "processed" if graph_nodes else "captured",
        "trigger": trigger.as_dict(),
        "evidence_ids": evidence_ids,
        "cleaned_evidence": clean_evidence_window(records, trigger),
        "graph_nodes": graph_nodes,
    }


def _nodes_for_evidence(nodes: list[dict[str, Any]], evidence_ids: list[str]) -> list[dict[str, Any]]:
    evidence_set = set(evidence_ids)
    matched: list[dict[str, Any]] = []
    for node in nodes:
        metadata = node.get("metadata") if isinstance(node.get("metadata"), dict) else {}
        node_evidence = {str(node.get("evidence_id") or "")}
        meta_evidence = metadata.get("evidence_ids")
        if isinstance(meta_evidence, list):
            node_evidence.update(str(item) for item in meta_evidence)
        if evidence_set.intersection(node_evidence):
            matched.append(node)
    return matched[:25]


def _evidence_roots(settings: Settings) -> list[Path]:
    roots: list[Path] = [settings.evidence_dir]
    workspace = os.getenv("AMO_WORKSPACE_CWD") or os.getcwd()
    try:
        spool = Path(workspace).expanduser().resolve() / ".amo-spool" / "evidence"
        if spool != settings.evidence_dir and spool.exists():
            roots.append(spool)
    except OSError:
        pass
    return roots

