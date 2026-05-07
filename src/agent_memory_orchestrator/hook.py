from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import Settings
from .memory_service import MemoryService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest one Claude/Codex hook payload")
    parser.add_argument("--agent", default="codex", choices=["claude", "codex", "user", "system"])
    parser.add_argument("--file", type=Path, help="JSON payload file. Defaults to stdin.")
    parser.add_argument("--amo-home", type=Path, help="AMO home directory containing config.json and .data.")
    parser.add_argument("--query", help="Manual smoke-test query. Used only when no payload file/stdin is provided.")
    parser.add_argument("--event-name", default="UserPromptSubmit", help="Manual smoke-test hook event name.")
    parser.add_argument("--session-id", default="manual-smoke", help="Manual smoke-test session id.")
    args = parser.parse_args(argv)

    if args.amo_home:
        os.environ["AMO_HOME"] = str(args.amo_home.expanduser().resolve())

    manual_smoke = bool(not args.file and sys.stdin.isatty())
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
    svc = MemoryService(settings)
    try:
        svc.init_db()
        if manual_smoke:
            additional_context = svc.build_hook_context(payload, default_agent=args.agent)
            result: dict[str, object] = {
                "continue": True,
                "manualSmoke": True,
                "ingested": False,
                "approvalMode": settings.approval_mode,
                "note": (
                    "No hook JSON was received on stdin, so AMO did not ingest an event. "
                    "Pass --query to smoke-test retrieval context."
                ),
            }
            if additional_context:
                result["hookSpecificOutput"] = {
                    "hookEventName": args.event_name,
                    "additionalContext": additional_context,
                }
                result["wouldAutoInject"] = settings.approval_mode == "auto_safe"
            print(json.dumps(result, indent=2))
            return 0

        result = svc.codex_hook_response(payload, default_agent=args.agent)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
