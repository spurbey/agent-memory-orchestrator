from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....graph.service import GraphRagService
from ....memory import MemoryService

BOOTSTRAP_COMMANDS = ("init-db", "init-graph")


def add_bootstrap_subcommands(sub: Any) -> None:
    sub.add_parser("init-db", help="Initialize local database schema")
    sub.add_parser("init-graph", help="Initialize local Kuzu GraphRAG schema")


def handle_bootstrap_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run local store initialization commands."""
    if args.command == "init-db":
        settings = Settings.load()
        svc = MemoryService(settings)
        try:
            svc.init_db()
        finally:
            svc.close()
        emit({"ok": True, "db_path": str(settings.db_path)})
        return 0

    if args.command == "init-graph":
        settings = Settings.load()
        graph = GraphRagService(settings)
        try:
            emit({"ok": True, "graph_path": str(settings.graph_path), "backend": settings.graph_backend})
        finally:
            graph.close()
        return 0

    return None


__all__ = ["BOOTSTRAP_COMMANDS", "add_bootstrap_subcommands", "handle_bootstrap_command"]
