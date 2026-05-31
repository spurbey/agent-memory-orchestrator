from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....graph.diagnostics import debug_hooks, debug_qwen
from ....memory import MemoryService
from ...daemon.client import DaemonClient, DaemonUnavailable


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


def handle_debug_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run debug CLI commands."""
    if args.command != "debug":
        return None

    settings = Settings.load()
    if args.debug_command == "hooks":
        emit(debug_hooks(settings))
        return 0
    if args.debug_command == "qwen":
        emit(debug_qwen(settings, sample=args.sample))
        return 0
    if args.debug_command in {"drain", "retrieval", "graph"}:
        client = DaemonClient.from_settings(settings, timeout_seconds=30)
        try:
            if args.debug_command == "drain":
                emit(client.get("/api/debug/drain", {"session_id": args.session_id}))
            elif args.debug_command == "graph":
                emit(client.get("/api/debug/graph", {"session_id": args.session_id}))
            else:
                emit(client.post("/graph/search", {"query": args.query, "limit": args.limit, "debug": True}))
        except DaemonUnavailable as exc:
            emit({"ok": False, "requires_daemon": True, "error": str(exc)})
            return 1
        return 0
    return None


__all__ = ["handle_debug_command", "rebuild_clean_db"]
