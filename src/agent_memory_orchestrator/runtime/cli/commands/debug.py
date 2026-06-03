from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ...diagnostics import debug_hooks, debug_qwen
from ...daemon.client import DaemonClient, DaemonUnavailable
from .memory import rebuild_clean_db


def add_debug_subcommands(sub: Any) -> None:
    debug = sub.add_parser("debug", help="Debug AMO hook, drain, Qwen, graph, and retrieval stages")
    debug_sub = debug.add_subparsers(dest="debug_command", required=True)
    debug_sub.add_parser("hooks", help="Check hook config, log, and latest evidence")
    debug_drain = debug_sub.add_parser("drain", help="Show pending drain cursor/evidence state")
    debug_drain.add_argument("--session-id", default="")
    debug_qwen_cmd = debug_sub.add_parser("qwen", help="Check Qwen availability and query-planner JSON")
    debug_qwen_cmd.add_argument("--sample", default="what did we decide about codex hooks")
    debug_graph_cmd = debug_sub.add_parser("graph", help="Show graph status and current context")
    debug_graph_cmd.add_argument("--session-id", default="")
    debug_retrieval = debug_sub.add_parser("retrieval", help="Show retrieval output through daemon")
    debug_retrieval.add_argument("--query", required=True)
    debug_retrieval.add_argument("--limit", type=int, default=8)


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


__all__ = ["add_debug_subcommands", "handle_debug_command", "rebuild_clean_db"]
