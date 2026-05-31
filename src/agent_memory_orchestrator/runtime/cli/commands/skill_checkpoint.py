"""CLI command group handling for skill checkpoints."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

from ....core.config import Settings
from ....skill_checkpoint import DEFAULT_LOCAL_NUM_CTX
from ....skill_checkpoint import DEFAULT_NUM_PREDICT
from ....skill_checkpoint import list_skill_checkpoints
from ....skill_checkpoint import mark_skill_checkpoint
from ....skill_checkpoint import run_local_skill_checkpoint_extraction
from ....skill_checkpoint import write_skill_checkpoint_outputs


def handle_skill_checkpoint_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run skill-checkpoint extraction, marking, status, and finalization commands."""
    if args.command != "skill-checkpoint":
        return None

    if getattr(args, "amo_home", None):
        os.environ["AMO_HOME"] = str(args.amo_home.expanduser().resolve())
    if args.skill_checkpoint_command == "extract":
        settings = Settings.load()
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        report = run_local_skill_checkpoint_extraction(
            packet=packet,
            settings=settings,
            out_dir=args.out_dir,
            num_ctx=args.num_ctx,
            num_predict=args.num_predict,
            timeout_seconds=args.timeout_seconds,
            auto_repair_validation_refs=not args.no_auto_repair_validation_refs,
        )
    elif args.skill_checkpoint_command == "mark":
        settings = Settings.load()
        report = mark_skill_checkpoint(
            settings=settings,
            agent=args.agent,
            session_id=args.session_id,
            note=args.note,
            mode=args.mode,
            cwd=args.cwd,
        )
    elif args.skill_checkpoint_command == "status":
        settings = Settings.load()
        report = list_skill_checkpoints(settings, limit=args.limit)
    elif args.skill_checkpoint_command == "finalize":
        packet = json.loads(args.packet.read_text(encoding="utf-8"))
        result = json.loads(args.result.read_text(encoding="utf-8"))
        report = write_skill_checkpoint_outputs(
            result=result,
            packet=packet,
            out_dir=args.out_dir,
            auto_repair_validation_refs=not args.no_auto_repair_validation_refs,
        )
    else:
        emit({"ok": False, "error": f"unknown skill-checkpoint command: {args.skill_checkpoint_command}"})
        return 2

    ok = bool(report.get("ok", report.get("status") == "accepted"))
    emit({"ok": ok, "result": report})
    return 0 if ok else 1


__all__ = ["DEFAULT_LOCAL_NUM_CTX", "DEFAULT_NUM_PREDICT", "handle_skill_checkpoint_command"]
