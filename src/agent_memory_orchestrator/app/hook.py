from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..integrations.adapters import normalize_adapter_event
from ..core.config import Settings
from ..evidence.raw_store import RawEvidenceRef
from ..evidence.raw_store import RawEvidenceStore


DEFAULT_STDIN_TIMEOUT_SECONDS = 2.0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Capture one Claude/Codex hook payload into the AMO graph evidence spool")
    parser.add_argument("--agent", default="codex", choices=["claude", "codex", "user", "system"])
    parser.add_argument("--file", type=Path, help="JSON payload file. Defaults to stdin.")
    parser.add_argument("--amo-home", type=Path, help="AMO home directory containing config.json, graph, and evidence.")
    parser.add_argument("--query", help="Manual smoke-test payload text. This does not trigger retrieval.")
    parser.add_argument("--event-name", default="UserPromptSubmit", help="Manual smoke-test hook event name.")
    parser.add_argument("--session-id", default="manual-smoke", help="Manual smoke-test session id.")
    args = parser.parse_args(argv)

    if args.amo_home:
        os.environ["AMO_HOME"] = str(args.amo_home.expanduser().resolve())

    try:
        _write_hook_log(
            "start",
            agent=args.agent,
            has_file=bool(args.file),
            has_query=bool(args.query),
            stdin_isatty=_stdin_isatty(),
            cwd=os.getcwd(),
        )
        payload, manual_smoke, input_info = _load_payload(args)
        _write_hook_log("input_loaded", manual_smoke=manual_smoke, **input_info)
        if payload is None:
            result = _no_payload_response(input_info)
            _write_hook_log("no_payload", response="continue")
            if _emit_debug_output(manual_smoke):
                print(json.dumps(result, indent=2))
            return 0

        settings = Settings.load()
        captured = _capture_without_graph(settings, payload, args.agent)
        result = _hook_response(captured, manual_smoke=manual_smoke)
        _write_hook_log(
            "capture_success",
            session_id=captured.get("session_id"),
            event_type=captured.get("event_type"),
            evidence=(captured.get("evidence") or {}).get("id") if isinstance(captured.get("evidence"), dict) else "",
            fallback_spool=bool(captured.get("fallback_spool")),
        )
        if _emit_debug_output(manual_smoke):
            print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Hooks should fail open: Codex should not hang or stop because AMO
        # cannot read/write its local graph, Kuzu package, or model cache.
        _write_hook_log("capture_failed", error_type=type(exc).__name__, error=str(exc))
        if _emit_debug_output(bool(args.query)):
            print(
                json.dumps(
                    {
                        "continue": True,
                        "captureOnly": True,
                        "ingested": False,
                        "evidence": {},
                        "systemMessage": f"AMO hook failed open: {exc}",
                    },
                    indent=2,
                )
            )
        return 0


def _load_payload(args: argparse.Namespace) -> tuple[dict[str, Any] | None, bool, dict[str, object]]:
    if args.query:
        return _manual_payload(args), True, {"input_source": "query", "raw_chars": 0, "timed_out": False}

    if args.file:
        start = time.monotonic()
        raw = args.file.read_text(encoding="utf-8")
        return _parse_raw_payload(raw, {"input_source": "file", "elapsed_ms": _elapsed_ms(start)})

    if _stdin_isatty():
        return _manual_payload(args), True, {"input_source": "interactive-stdin", "raw_chars": 0, "timed_out": False}

    timeout_seconds = _stdin_timeout_seconds()
    start = time.monotonic()
    raw, timed_out, read_error = _read_stdin_with_timeout(timeout_seconds)
    input_info: dict[str, object] = {
        "input_source": "stdin",
        "raw_chars": len(raw),
        "timed_out": timed_out,
        "timeout_seconds": timeout_seconds,
        "elapsed_ms": _elapsed_ms(start),
    }
    if read_error is not None:
        input_info["read_error"] = f"{type(read_error).__name__}: {read_error}"
        return None, False, input_info
    if timed_out:
        return None, False, input_info
    return _parse_raw_payload(raw, input_info)


def _manual_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "hook_event_name": args.event_name,
        "session_id": args.session_id,
        "prompt": args.query or "",
        "source_app": "manual",
    }


def _parse_raw_payload(raw: str, input_info: dict[str, object]) -> tuple[dict[str, Any] | None, bool, dict[str, object]]:
    stripped = raw.strip()
    if not stripped:
        input_info["empty"] = True
        return None, False, input_info
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("hook payload must be a JSON object")
    input_info["payload_keys"] = sorted(str(key) for key in payload.keys())[:20]
    return payload, False, input_info


def _read_stdin_with_timeout(timeout_seconds: float) -> tuple[str, bool, BaseException | None]:
    results: queue.Queue[tuple[str, BaseException | None]] = queue.Queue(maxsize=1)

    def _reader() -> None:
        try:
            results.put((sys.stdin.read(), None))
        except BaseException as exc:  # pragma: no cover - defensive around host-provided stdin
            results.put(("", exc))

    thread = threading.Thread(target=_reader, name="amo-hook-stdin-reader", daemon=True)
    thread.start()
    try:
        raw, error = results.get(timeout=max(0.01, timeout_seconds))
        return raw, False, error
    except queue.Empty:
        return "", True, None


def _stdin_timeout_seconds() -> float:
    raw_ms = os.getenv("AMO_HOOK_STDIN_TIMEOUT_MS")
    if raw_ms:
        try:
            return max(0.05, float(raw_ms) / 1000.0)
        except ValueError:
            return DEFAULT_STDIN_TIMEOUT_SECONDS
    raw = os.getenv("AMO_HOOK_STDIN_TIMEOUT_SECONDS")
    if raw:
        try:
            return max(0.05, float(raw))
        except ValueError:
            return DEFAULT_STDIN_TIMEOUT_SECONDS
    return DEFAULT_STDIN_TIMEOUT_SECONDS


def _stdin_isatty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except Exception:
        return False


def _elapsed_ms(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _no_payload_response(input_info: dict[str, object]) -> dict[str, object]:
    return {
        "continue": True,
        "captureOnly": True,
        "ingested": False,
        "noPayload": True,
        "input": input_info,
        "systemMessage": "AMO hook received no JSON payload on stdin; capture skipped and Codex continued.",
    }


def _emit_debug_output(manual_smoke: bool) -> bool:
    return manual_smoke or os.getenv("AMO_HOOK_DEBUG_OUTPUT") == "1"


def _hook_response(captured: dict[str, object], *, manual_smoke: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "continue": True,
        "manualSmoke": manual_smoke,
        "captureOnly": True,
        "ingested": True,
        "session_id": captured.get("session_id"),
        "event_type": captured.get("event_type"),
        "evidence": captured.get("evidence"),
        "fallback_spool": bool(captured.get("fallback_spool")),
        "merge": captured.get("merge"),
        "note": "Hooks capture evidence only. Use MCP tool amo_graph_search for explicit memory retrieval.",
    }
    additional_context = str(captured.get("additional_context") or "").strip()
    if additional_context:
        result["hookSpecificOutput"] = {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context,
        }
    return result


def _capture_without_graph(settings: Settings, payload: dict[str, Any], agent: str) -> dict[str, object]:
    normalized = normalize_adapter_event(payload, default_agent=agent) or _fallback_event(payload, agent)
    session_id = str(normalized["session_id"])
    event_type = str(normalized["event_type"])
    source_app = str(normalized["source_app"])
    evidence, fallback_spool = _append_evidence(
        settings,
        payload,
        session_id=session_id,
        source_app=source_app,
        event_name=event_type,
    )
    return {
        "ok": True,
        "session_id": session_id,
        "event_type": event_type,
        "source_app": source_app,
        "evidence": evidence.as_dict(),
        "fallback_spool": fallback_spool,
        "merge": {"merged": False, "reason": "hook_capture_only"},
        "additional_context": _startup_context(event_type, session_id, source_app, evidence),
        "capture_only": True,
    }


def _append_evidence(
    settings: Settings,
    payload: dict[str, Any],
    *,
    session_id: str,
    source_app: str,
    event_name: str,
) -> tuple[RawEvidenceRef, bool]:
    try:
        return (
            RawEvidenceStore(settings.evidence_dir).append(
                payload,
                session_id=session_id,
                source_app=source_app,
                event_name=event_name,
            ),
            False,
        )
    except PermissionError:
        return (
            RawEvidenceStore(_fallback_evidence_dir(payload)).append(
                payload,
                session_id=session_id,
                source_app=source_app,
                event_name=event_name,
            ),
            True,
        )


def _fallback_evidence_dir(payload: dict[str, Any]) -> Path:
    cwd = payload.get("cwd") or os.getenv("AMO_WORKSPACE_CWD") or os.getcwd()
    try:
        root = Path(str(cwd)).expanduser().resolve()
    except Exception:
        root = Path(tempfile.gettempdir()).resolve()
    return root / ".amo-spool" / "evidence"


def _startup_context(event_type: str, session_id: str, source_app: str, evidence: RawEvidenceRef) -> str:
    if event_type != "session_start":
        return ""
    return (
        "AMO GraphRAG capture is active. "
        "Hooks only capture evidence; use MCP tool amo_graph_search for explicit memory retrieval. "
        f"session={session_id} app={source_app} evidence={evidence.id}"
    )


def _fallback_event(payload: dict[str, Any], agent: str) -> dict[str, object]:
    event_name = _snake(str(payload.get("hook_event_name") or payload.get("event_type") or "message"))
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "default")
    return {
        "session_id": session_id,
        "agent": agent,
        "event_type": event_name,
        "content": payload.get("prompt") or payload.get("content") or payload.get("message") or "",
        "metadata": {},
        "created_at": payload.get("created_at") or payload.get("timestamp"),
        "source_app": agent,
    }


def _snake(value: str) -> str:
    import re

    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", value)
    return re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower() or "message"


def _write_hook_log(event: str, **fields: object) -> None:
    record = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "pid": os.getpid(),
        **fields,
    }
    line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
    for path in _hook_log_candidates():
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except Exception:
            continue


def _hook_log_candidates() -> list[Path]:
    candidates: list[Path] = []
    home = os.getenv("AMO_HOME")
    if home:
        candidates.append(Path(home).expanduser().resolve() / "logs" / "hook.log")
    try:
        candidates.append(Path.cwd().resolve() / ".amo-spool" / "logs" / "hook.log")
    except Exception:
        pass
    candidates.append(Path(tempfile.gettempdir()).resolve() / "agent-memory-orchestrator" / "hook.log")
    return candidates


if __name__ == "__main__":
    raise SystemExit(main())

