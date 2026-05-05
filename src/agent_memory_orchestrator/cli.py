from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .memory_service import MemoryService
from .orchestrator import OrchestratorService


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize local database schema")

    ingest = sub.add_parser("ingest-transcript", help="Ingest JSONL transcript")
    ingest.add_argument("--agent", required=True, choices=["claude", "codex", "user", "system"])
    ingest.add_argument("--file", required=True, type=Path)
    ingest.add_argument("--session-id", required=True)
    ingest.add_argument("--session-title")

    hook = sub.add_parser("ingest-hook", help="Ingest one Claude/Codex hook JSON payload")
    hook.add_argument("--agent", default="codex", choices=["claude", "codex", "user", "system"])
    hook.add_argument("--file", required=True, type=Path)

    codex_import = sub.add_parser("import-codex-sessions", help="Import Codex rollout JSONL sessions")
    codex_import.add_argument("--root", type=Path, default=Path.home() / ".codex" / "sessions")
    codex_import.add_argument("--limit", type=int, default=30)

    sub.add_parser("print-codex-hooks", help="Print a Codex hooks.json snippet for AMO hot-path capture/retrieval")

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("--query", required=True)
    search.add_argument("--session-id")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-historical", action="store_true")

    timeline = sub.add_parser("timeline", help="View session timeline")
    timeline.add_argument("--session-id", required=True)
    timeline.add_argument("--limit", type=int, default=50)

    export_cmd = sub.add_parser("export", help="Export snapshot to JSONL")
    export_cmd.add_argument("--out", required=True, type=Path)
    export_cmd.add_argument("--session-id")

    import_cmd = sub.add_parser("import", help="Import snapshot JSONL")
    import_cmd.add_argument("--file", required=True, type=Path)

    summary = sub.add_parser("session-summary", help="Generate deterministic session summary")
    summary.add_argument("--session-id", required=True)

    sub.add_parser("metrics", help="Inspect pipeline/retrieval row counts and latest retrieval")
    sub.add_parser("rebuild-indexes", help="Rebuild FTS/vector index rows from canonical memory_units")

    orch_start = sub.add_parser("orchestrate-start", help="Start orchestrator session")
    orch_start.add_argument("--session-id", required=True)
    orch_start.add_argument("--title")

    orch_submit = sub.add_parser("orchestrate-submit", help="Submit orchestrator round")
    orch_submit.add_argument("--session-id", required=True)
    orch_submit.add_argument("--agent", required=True, choices=["claude", "codex"])
    orch_submit.add_argument("--summary", required=True)
    orch_submit.add_argument("--confidence", required=True, type=float)
    orch_submit.add_argument("--artifact-uri", default="")
    orch_submit.add_argument("--blocking-issue", action="append", default=[])

    orch_status = sub.add_parser("orchestrate-status", help="Get orchestrator status")
    orch_status.add_argument("--session-id", required=True)

    orch_decide = sub.add_parser("orchestrate-decision", help="Apply user decision")
    orch_decide.add_argument("--session-id", required=True)
    orch_decide.add_argument("--decision", required=True, choices=["approved", "rejected"])
    orch_decide.add_argument("--notes", default="")
    orch_decide.add_argument("--decided-by", default="user")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = Settings.load()

    try:
        if args.command == "init-db":
            svc = MemoryService(settings)
            try:
                svc.init_db()
            finally:
                svc.close()
            _print({"ok": True, "db_path": str(settings.db_path)})
            return 0

        if args.command in {
            "ingest-transcript",
            "ingest-hook",
            "import-codex-sessions",
            "search",
            "timeline",
            "export",
            "import",
            "session-summary",
            "metrics",
            "rebuild-indexes",
            "print-codex-hooks",
        }:
            svc = MemoryService(settings)
            try:
                svc.init_db()
                if args.command == "ingest-transcript":
                    result = svc.ingest_transcript(
                        agent=args.agent,
                        file_path=args.file,
                        session_id=args.session_id,
                        session_title=args.session_title,
                    )
                    _print({"ok": True, **result})
                elif args.command == "ingest-hook":
                    payload = json.loads(args.file.read_text(encoding="utf-8"))
                    result = svc.ingest_hook_payload(payload, default_agent=args.agent)
                    _print({"ok": True, **result})
                elif args.command == "import-codex-sessions":
                    result = svc.import_codex_sessions(args.root, limit=args.limit)
                    _print({"ok": True, "result": result})
                elif args.command == "print-codex-hooks":
                    _print({"ok": True, "hooks": _codex_hooks_snippet()})
                elif args.command == "search":
                    results = svc.search_memories(
                        args.query,
                        session_id=args.session_id,
                        limit=args.limit,
                        include_historical=args.include_historical,
                    )
                    _print({"ok": True, "count": len(results), "results": results})
                elif args.command == "timeline":
                    events = svc.timeline(args.session_id, limit=args.limit)
                    _print({"ok": True, "count": len(events), "events": events})
                elif args.command == "export":
                    rows = svc.export_snapshot(args.out, session_id=args.session_id)
                    _print({"ok": True, "rows": rows, "out": str(args.out.resolve())})
                elif args.command == "import":
                    rows = svc.import_snapshot(args.file)
                    _print({"ok": True, "rows": rows, "source": str(args.file.resolve())})
                elif args.command == "session-summary":
                    result = svc.generate_session_summary(args.session_id)
                    _print({"ok": True, "result": result})
                elif args.command == "metrics":
                    _print({"ok": True, "result": svc.inspect_metrics()})
                elif args.command == "rebuild-indexes":
                    _print({"ok": True, "result": svc.rebuild_indexes()})
            finally:
                svc.close()
            return 0

        orch = OrchestratorService(settings)
        try:
            if args.command == "orchestrate-start":
                payload = orch.start(session_id=args.session_id, title=args.title)
            elif args.command == "orchestrate-submit":
                payload = orch.submit(
                    session_id=args.session_id,
                    agent=args.agent,
                    summary=args.summary,
                    confidence=args.confidence,
                    artifact_uri=args.artifact_uri,
                    blocking_issues=args.blocking_issue,
                )
            elif args.command == "orchestrate-status":
                payload = orch.status(session_id=args.session_id)
            elif args.command == "orchestrate-decision":
                payload = orch.user_decision(
                    session_id=args.session_id,
                    decision=args.decision,
                    notes=args.notes,
                    decided_by=args.decided_by,
                )
            else:
                parser.error(f"unknown command: {args.command}")
                return 2
            _print({"ok": True, "result": payload})
        finally:
            orch.close()
        return 0
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


def _codex_hooks_snippet() -> dict:
    command = "python -m agent_memory_orchestrator.hook --agent codex"
    return {
        "features": {"codex_hooks": True},
        "hooks": {
            "SessionStart": [
                {
                    "matcher": "startup|resume|clear",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 30,
                            "statusMessage": "AMO loading local memory context",
                        }
                    ],
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 30,
                            "statusMessage": "AMO retrieving local memory",
                        }
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 30,
                            "statusMessage": "AMO capturing tool result",
                        }
                    ],
                }
            ],
            "Stop": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": command,
                            "timeout": 30,
                            "statusMessage": "AMO summarizing turn",
                        }
                    ]
                }
            ],
        },
    }


if __name__ == "__main__":
    raise SystemExit(main())
