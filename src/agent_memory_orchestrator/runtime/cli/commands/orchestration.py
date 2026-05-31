"""CLI command group handling for local orchestration workflows."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....orchestration import OrchestratorService

ORCHESTRATION_COMMANDS = (
    "orchestrate-start",
    "orchestrate-submit",
    "orchestrate-status",
    "orchestrate-decision",
)


def handle_orchestration_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run local orchestration commands."""
    if args.command not in ORCHESTRATION_COMMANDS:
        return None

    settings = Settings.load()
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
            return None
        emit({"ok": True, "result": payload})
    finally:
        orch.close()
    return 0


__all__ = ["ORCHESTRATION_COMMANDS", "handle_orchestration_command"]
