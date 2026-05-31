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


__all__ = ["MEMORY_COMMANDS", "handle_memory_command", "rebuild_clean_db"]
