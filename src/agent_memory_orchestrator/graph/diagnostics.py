from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..config import Settings
from ..app.client import DaemonClient, DaemonUnavailable
from ..evidence.drain import EvidenceDrain
from ..evidence.triggers import detect_trigger
from ..qwen_client import OllamaQwenClient, QwenUnavailable
from .merge import QwenMergeClassifier
from .service import GraphRagService


def debug_hooks(settings: Settings, *, user_home: Path | None = None) -> dict[str, Any]:
    home = user_home or Path.home()
    config_path = home / ".codex" / "config.toml"
    hooks_path = home / ".codex" / "hooks.json"
    log_path = settings.home / "logs" / "hook.log"
    latest_evidence = _latest_jsonl_line(settings.evidence_dir)
    return {
        "ok": True,
        "codex_config": str(config_path),
        "codex_config_exists": config_path.exists(),
        "codex_config_has_hook": "agent_memory_orchestrator" in _safe_read(config_path),
        "codex_hooks_json": str(hooks_path),
        "codex_hooks_json_exists": hooks_path.exists(),
        "hook_log": str(log_path),
        "hook_log_exists": log_path.exists(),
        "latest_hook_log": _tail(log_path, 5),
        "latest_evidence": latest_evidence,
    }


def debug_drain(drain: EvidenceDrain, *, session_id: str = "") -> dict[str, Any]:
    return drain.pending(session_id=session_id)


def debug_qwen(settings: Settings, *, sample: str = "Classify a decision lookup query.") -> dict[str, Any]:
    start = time.monotonic()
    try:
        plan = OllamaQwenClient(
            endpoint=settings.qwen_endpoint,
            model=settings.qwen_model,
            timeout_seconds=settings.qwen_timeout_seconds,
            planner_timeout_seconds=min(settings.qwen_timeout_seconds, settings.qwen_planner_timeout_seconds),
            num_ctx=settings.qwen_num_ctx,
        ).plan_query(sample)
        merge_start = time.monotonic()
        merge_classifier: dict[str, Any]
        try:
            merge_result = QwenMergeClassifier(settings).classify(
                {
                    "id": "draft:debug",
                    "kind": "Decision",
                    "summary": "Use commit merge engine for central graph versioning.",
                },
                {
                    "id": "central:debug",
                    "kind": "Decision",
                    "summary": "Use deterministic central graph versioning.",
                },
                {"total": 0.58, "same_kind": True},
            )
            merge_classifier = {"ok": True, "elapsed_ms": _elapsed_ms(merge_start), "result": merge_result}
        except QwenUnavailable as exc:
            merge_classifier = {"ok": False, "elapsed_ms": _elapsed_ms(merge_start), "error": str(exc)}
        return {
            "ok": True,
            "model": settings.qwen_model,
            "elapsed_ms": _elapsed_ms(start),
            "plan": plan.as_dict(),
            "merge_classifier": merge_classifier,
        }
    except QwenUnavailable as exc:
        return {"ok": False, "model": settings.qwen_model, "elapsed_ms": _elapsed_ms(start), "error": str(exc)}


def debug_graph(graph: GraphRagService, *, session_id: str = "") -> dict[str, Any]:
    status = graph.merge_status(session_id=session_id)
    context = graph.current_context(session_id=session_id, limit=10)
    return {"ok": True, "status": status, "cache": graph.graph_cache_status(), "current_context": context}


def debug_retrieval(client: DaemonClient, *, query: str, limit: int = 8) -> dict[str, Any]:
    start = time.monotonic()
    try:
        result = client.post("/graph/search", {"query": query, "limit": limit, "debug": True})
        result.setdefault("timing", {})["client_elapsed_ms"] = _elapsed_ms(start)
        return result
    except DaemonUnavailable as exc:
        return {"ok": False, "error": str(exc), "client_elapsed_ms": _elapsed_ms(start)}


def trigger_preview(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pending_write = False
    decisions: list[dict[str, Any]] = []
    for record in records:
        decision = detect_trigger(record, pending_write=pending_write)
        if decision.is_write:
            pending_write = True
        if decision.should_process:
            pending_write = False
        decisions.append(
            {
                "id": record.get("id"),
                "session_id": record.get("session_id"),
                "event_name": record.get("event_name"),
                "decision": decision.as_dict(),
            }
        )
    return decisions


def _latest_jsonl_line(root: Path) -> dict[str, Any]:
    if not root.exists():
        return {}
    files = sorted(root.glob("*.jsonl"))
    if not files:
        return {}
    for line in reversed(files[-1].read_text(encoding="utf-8").splitlines()):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        return payload if isinstance(payload, dict) else {}
    return {}


def _tail(path: Path, count: int) -> list[str]:
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)
