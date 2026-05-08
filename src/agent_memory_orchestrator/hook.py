from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import Settings
from .graph_service import GraphRagService
from .raw_evidence import RawEvidenceStore


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

    manual_smoke = bool(args.query or (not args.file and sys.stdin.isatty()))
    raw = "" if manual_smoke else args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
    payload = (
        {
            "hook_event_name": args.event_name,
            "session_id": args.session_id,
            "prompt": args.query or "",
            "source_app": "manual",
        }
        if manual_smoke
        else json.loads(raw or "{}")
    )
    settings = Settings.load()
    service: GraphRagService | None = None
    try:
        service = GraphRagService(settings)
        captured = service.capture_hook(payload, default_agent=args.agent)
        result = _hook_response(captured, manual_smoke=manual_smoke)
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        # Hooks should fail open: Codex should not hang or stop because AMO
        # cannot read/write its local graph, Kuzu package, or model cache.
        spooled = _spool_without_graph(settings, payload, args.agent)
        print(
            json.dumps(
                {
                    "continue": True,
                    "captureOnly": True,
                    "ingested": bool(spooled),
                    "evidence": spooled,
                    "systemMessage": f"AMO hook failed open: {exc}",
                },
                indent=2,
            )
        )
        return 0
    finally:
        if service is not None:
            service.close()


def _hook_response(captured: dict[str, object], *, manual_smoke: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "continue": True,
        "manualSmoke": manual_smoke,
        "captureOnly": True,
        "ingested": True,
        "session_id": captured.get("session_id"),
        "event_type": captured.get("event_type"),
        "evidence": captured.get("evidence"),
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


def _spool_without_graph(settings: Settings, payload: dict, agent: str) -> dict[str, object]:
    try:
        session_id = str(payload.get("session_id") or payload.get("sessionId") or "default")
        event_name = str(payload.get("hook_event_name") or payload.get("event_type") or "message")
        evidence = RawEvidenceStore(settings.evidence_dir).append(
            payload,
            session_id=session_id,
            source_app=agent,
            event_name=event_name,
        )
        return evidence.as_dict()
    except Exception:
        return {}


if __name__ == "__main__":
    raise SystemExit(main())
