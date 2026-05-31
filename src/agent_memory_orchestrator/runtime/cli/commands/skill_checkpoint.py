"""CLI command group handling for skill checkpoints."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....skill_checkpoint import DEFAULT_LOCAL_NUM_CTX
from ....skill_checkpoint import DEFAULT_NUM_PREDICT
from ....skill_checkpoint import list_skill_checkpoints
from ....skill_checkpoint import mark_skill_checkpoint
from ....skill_checkpoint import run_local_skill_checkpoint_extraction
from ....skill_checkpoint import write_skill_checkpoint_outputs


def add_skill_checkpoint_subcommands(sub: Any) -> None:
    skill_checkpoint = sub.add_parser(
        "skill-checkpoint",
        help="Build or finalize a reusable skill from a compact checkpoint packet",
    )
    skill_checkpoint_sub = skill_checkpoint.add_subparsers(dest="skill_checkpoint_command", required=True)
    skill_checkpoint_extract = skill_checkpoint_sub.add_parser(
        "extract",
        help="Run local Ollama/Qwen over a compact checkpoint packet and write validated skill outputs",
    )
    skill_checkpoint_extract.add_argument("--packet", required=True, type=Path)
    skill_checkpoint_extract.add_argument("--out-dir", required=True, type=Path)
    skill_checkpoint_extract.add_argument("--amo-home", type=Path)
    skill_checkpoint_extract.add_argument("--num-ctx", type=int, default=DEFAULT_LOCAL_NUM_CTX)
    skill_checkpoint_extract.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    skill_checkpoint_extract.add_argument("--timeout-seconds", type=float)
    skill_checkpoint_extract.add_argument("--no-auto-repair-validation-refs", action="store_true")

    skill_checkpoint_mark = skill_checkpoint_sub.add_parser(
        "mark",
        help="Mark the current agent session as a pending skill checkpoint",
    )
    skill_checkpoint_mark.add_argument("--agent", choices=["codex", "claude"], default="codex")
    skill_checkpoint_mark.add_argument(
        "--session-id",
        default="",
        help="Optional explicit session id. Defaults to latest captured session for the agent.",
    )
    skill_checkpoint_mark.add_argument("--note", default="", help="Optional user intent or checkpoint note.")
    skill_checkpoint_mark.add_argument("--mode", choices=["workflow", "single_commit"], default="workflow")
    skill_checkpoint_mark.add_argument("--cwd", type=Path, default=Path.cwd())
    skill_checkpoint_mark.add_argument("--amo-home", type=Path)

    skill_checkpoint_status = skill_checkpoint_sub.add_parser(
        "status",
        help="List pending skill checkpoints",
    )
    skill_checkpoint_status.add_argument("--limit", type=int, default=20)
    skill_checkpoint_status.add_argument("--amo-home", type=Path)

    skill_checkpoint_finalize = skill_checkpoint_sub.add_parser(
        "finalize",
        help="Post-validate a Qwen skill-checkpoint result and render SKILL.md/provenance",
    )
    skill_checkpoint_finalize.add_argument("--result", required=True, type=Path)
    skill_checkpoint_finalize.add_argument("--packet", required=True, type=Path)
    skill_checkpoint_finalize.add_argument("--out-dir", required=True, type=Path)
    skill_checkpoint_finalize.add_argument("--amo-home", type=Path)
    skill_checkpoint_finalize.add_argument("--no-auto-repair-validation-refs", action="store_true")


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


__all__ = [
    "DEFAULT_LOCAL_NUM_CTX",
    "DEFAULT_NUM_PREDICT",
    "add_skill_checkpoint_subcommands",
    "handle_skill_checkpoint_command",
]
