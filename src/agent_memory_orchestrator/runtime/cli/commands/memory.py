from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....memory import MemoryService
from .install import codex_hooks_snippet

MEMORY_COMMANDS = {
    "ingest-transcript",
    "ingest-hook",
    "import-codex-sessions",
    "rebuild-clean-db",
    "search",
    "context-pack",
    "timeline",
    "export",
    "import",
    "session-summary",
    "metrics",
    "rebuild-indexes",
    "print-codex-hooks",
}


def add_memory_subcommands(sub: Any) -> None:
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
    codex_import.add_argument("--defer-vectors", action="store_true", help="Skip embeddings during import; run rebuild-indexes later.")
    codex_import.add_argument(
        "--include-existing",
        action="store_true",
        help="Reprocess sessions that already have imported events. Default skips them to avoid duplicates.",
    )

    clean = sub.add_parser("rebuild-clean-db", help="Create a fresh DB from raw Codex sessions")
    clean.add_argument("--out", required=True, type=Path)
    clean.add_argument("--codex-root", type=Path, default=Path.home() / ".codex" / "sessions")
    clean.add_argument("--limit", type=int, default=30)
    clean.add_argument("--force", action="store_true")

    sub.add_parser("print-codex-hooks", help="Print a Codex config.toml snippet for AMO capture-only hooks")

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("--query", required=True)
    search.add_argument("--session-id")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-historical", action="store_true")

    context = sub.add_parser("context-pack", help="Build an agent-ready memory context pack")
    context.add_argument("--query", required=True)
    context.add_argument("--session-id")
    context.add_argument("--budget", type=int, default=None)
    context.add_argument("--limit", type=int, default=12)
    context.add_argument("--include-historical", action="store_true")
    context.add_argument("--format", choices=["json", "text"], default="json")

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
    rebuild = sub.add_parser("rebuild-indexes", help="Rebuild FTS/vector index rows from canonical memory_units")
    rebuild.add_argument("--force-vectors", action="store_true")


def rebuild_clean_db(settings: Settings, out_path: Path, codex_root: Path, limit: int, force: bool) -> dict:
    target = out_path.resolve()
    if target.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing DB without --force: {target}")
    if force:
        for path in (target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
            if path.exists():
                path.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean_settings = replace(settings, db_path=target)
    svc = MemoryService(clean_settings)
    try:
        svc.init_db()
        result = svc.import_codex_sessions(codex_root, limit=limit)
        indexes = svc.rebuild_indexes(force_vectors=False)
        return {
            "out": str(target),
            "codex_root": str(codex_root.resolve()),
            "import": result,
            "indexes": indexes,
        }
    finally:
        svc.close()


def handle_memory_command(
    args: Any,
    *,
    emit: Callable[[object], None],
    emit_text: Callable[[str], None],
) -> int | None:
    """Run local memory database CLI commands."""
    if args.command not in MEMORY_COMMANDS:
        return None

    if args.command == "rebuild-clean-db":
        settings = Settings.load()
        result = rebuild_clean_db(settings, args.out, args.codex_root, args.limit, args.force)
        emit({"ok": True, "result": result})
        return 0

    settings = Settings.load()
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
            emit({"ok": True, **result})
        elif args.command == "ingest-hook":
            payload = json.loads(args.file.read_text(encoding="utf-8"))
            result = svc.ingest_hook_payload(payload, default_agent=args.agent)
            emit({"ok": True, **result})
        elif args.command == "import-codex-sessions":
            result = svc.import_codex_sessions(
                args.root,
                limit=args.limit,
                defer_vectors=args.defer_vectors,
                skip_existing=not args.include_existing,
            )
            emit({"ok": True, "result": result})
        elif args.command == "print-codex-hooks":
            emit({"ok": True, "hooks": codex_hooks_snippet()})
        elif args.command == "search":
            results = svc.search_memories(
                args.query,
                session_id=args.session_id,
                limit=args.limit,
                include_historical=args.include_historical,
            )
            emit({"ok": True, "count": len(results), "results": results})
        elif args.command == "context-pack":
            pack = svc.build_context_pack(
                args.query,
                session_id=args.session_id,
                budget_tokens=args.budget,
                limit=args.limit,
                include_historical=args.include_historical,
            )
            if args.format == "text":
                emit_text(pack["text"])
            else:
                emit({"ok": True, "result": pack})
        elif args.command == "timeline":
            events = svc.timeline(args.session_id, limit=args.limit)
            emit({"ok": True, "count": len(events), "events": events})
        elif args.command == "export":
            rows = svc.export_snapshot(args.out, session_id=args.session_id)
            emit({"ok": True, "rows": rows, "out": str(args.out.resolve())})
        elif args.command == "import":
            rows = svc.import_snapshot(args.file)
            emit({"ok": True, "rows": rows, "source": str(args.file.resolve())})
        elif args.command == "session-summary":
            result = svc.generate_session_summary(args.session_id)
            emit({"ok": True, "result": result})
        elif args.command == "metrics":
            emit({"ok": True, "result": svc.inspect_metrics()})
        elif args.command == "rebuild-indexes":
            emit({"ok": True, "result": svc.rebuild_indexes(force_vectors=args.force_vectors)})
    finally:
        svc.close()
    return 0


__all__ = ["MEMORY_COMMANDS", "add_memory_subcommands", "handle_memory_command", "rebuild_clean_db"]
