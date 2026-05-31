from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....graph.diagnostics import debug_hooks, debug_qwen
from ...daemon.client import DaemonClient, DaemonUnavailable
from .memory import rebuild_clean_db


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
