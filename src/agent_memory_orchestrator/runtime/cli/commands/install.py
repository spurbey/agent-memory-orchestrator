from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....core.privacy import redact_secrets
from ....application.services.graph_rag import GraphRagService
from ....infrastructure.kuzu import GraphBackendUnavailable
from ....install.service import InstallOptions
from ....install.service import apply_install_plan
from ....install.service import build_install_plan
from ....install.service import doctor as install_doctor
from ....install.service import uninstall as uninstall_targets
from ....llm.models import download_models
from ....memory import MemoryService
from ....application.pipeline.storage_lifecycle import initialize_fresh_production_storage

INSTALL_COMMANDS = ("install", "doctor", "uninstall")


def codex_hooks_snippet() -> dict:
    command = "python -m agent_memory_orchestrator.runtime.hook.launcher --agent codex"
    return {
        "format": "toml",
        "snippet": "\n".join(
            [
                "[features]",
                "codex_hooks = true",
                "",
                "[[hooks.SessionStart]]",
                'matcher = "startup|resume|clear"',
                "[[hooks.SessionStart.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO starting graph capture"',
                "",
                "[[hooks.UserPromptSubmit]]",
                "[[hooks.UserPromptSubmit.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing prompt evidence"',
                "",
                "[[hooks.PostToolUse]]",
                'matcher = "*"',
                "[[hooks.PostToolUse.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing tool evidence"',
                "",
                "[[hooks.Stop]]",
                "[[hooks.Stop.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing session stop"',
            ]
        ),
    }


def add_model_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=["cpu-light", "cpu-balanced", "gpu-quality"],
        default="cpu-balanced",
        help="Hardware-oriented model preset.",
    )
    parser.add_argument("--embedding-model", help="Override preset embedding model.")
    parser.add_argument("--reranker-model", help="Override preset reranker model.")
    parser.add_argument("--qwen-model", help="Override preset Ollama Qwen model.")


def add_install_subcommands(sub: Any) -> None:
    install = sub.add_parser("install", help="Configure Claude/Codex hooks, MCP, and local AMO runtime config")
    install.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    install.add_argument("--user-home", type=Path, default=Path.home(), help="Home directory containing .codex/.claude")
    install.add_argument(
        "--amo-home",
        type=Path,
        default=Path.home() / ".agent-memory-orchestrator",
        help="AMO data/config home used by hooks and MCP.",
    )
    add_model_selection_args(install)
    install.add_argument(
        "--python-command",
        default=sys.executable or "python",
        help="Python executable visible to Claude/Codex hooks.",
    )
    install.add_argument("--download-models", action="store_true", help="Download selected local models during install.")
    install.add_argument("--skip-init-db", action="store_true", help="Do not initialize the AMO SQLite database.")
    install.add_argument("--dry-run", action="store_true", help="Show planned changes without writing files.")
    install.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")
    install.add_argument("--json", action="store_true", help="Print machine-readable install details.")
    install.add_argument("--force", action="store_true", help="Overwrite existing AMO target entries when safe.")

    doctor_cmd = sub.add_parser("doctor", help="Check AMO install/config status")
    doctor_cmd.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    doctor_cmd.add_argument("--user-home", type=Path, default=Path.home())
    doctor_cmd.add_argument("--amo-home", type=Path, default=Path.home() / ".agent-memory-orchestrator")

    uninstall_cmd = sub.add_parser("uninstall", help="Remove AMO-managed Claude/Codex config entries")
    uninstall_cmd.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    uninstall_cmd.add_argument("--user-home", type=Path, default=Path.home())
    uninstall_cmd.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")


def confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def summarize_install_plan(plan: dict) -> dict:
    operations = []
    for op in plan["operations"]:
        path = Path(op["path"])
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        after = op["after"]
        safe_after = redact_secrets(after)[0]
        operations.append(
            {
                "target": op["target"],
                "path": op["path"],
                "description": op["description"],
                "exists": op["exists"],
                "changed": before != after,
                "after_preview": safe_after[:2000],
                "after_truncated": len(safe_after) > 2000,
            }
        )
    return {
        "target": plan["target"],
        "targets": plan["targets"],
        "user_home": plan["user_home"],
        "amo_home": plan["amo_home"],
        "models": plan["models"],
        "operations": operations,
        "notes": plan["notes"],
    }


def format_install_plan(summary: dict, *, dry_run: bool = False) -> str:
    models = summary.get("models", {})
    operations = summary.get("operations", [])
    changed = [op for op in operations if op.get("changed")]
    unchanged = [op for op in operations if not op.get("changed")]

    lines = ["AMO install plan"]
    if dry_run:
        lines[0] = "AMO install dry run"
    lines.extend(
        [
            f"- Target: {', '.join(summary.get('targets', [])) or summary.get('target', 'all')}",
            f"- AMO home: {summary.get('amo_home', '')}",
            f"- Preset: {models.get('preset', '')}",
            f"- Qwen model: {models.get('qwen_model', '')}",
            f"- Embeddings: {models.get('embedding_model', '')}",
            f"- Reranker: {models.get('reranker_model', '')}",
            "",
            "Changes to apply:",
        ]
    )
    if changed:
        lines.extend(f"- {_operation_label(op)}" for op in changed)
    else:
        lines.append("- No file changes needed.")
    if unchanged:
        lines.append(f"- {len(unchanged)} item(s) already up to date.")
    lines.extend(
        [
            "",
            "Existing files are backed up before they are changed.",
            "Use --json to inspect exact paths and generated config.",
        ]
    )
    if dry_run:
        lines.append("No files changed.")
    return "\n".join(lines)


def format_install_result(payload: dict) -> str:
    plan = payload.get("plan", {})
    results = payload.get("apply", {}).get("results", [])
    changed = [item for item in results if item.get("changed")]
    unchanged = [item for item in results if not item.get("changed")]
    targets = ", ".join(plan.get("targets", [])) or plan.get("target", "all")

    lines = [
        "AMO install complete.",
        f"- Target: {targets}",
        f"- AMO home: {plan.get('amo_home', '')}",
    ]
    if changed:
        lines.append("")
        lines.append("Updated:")
        lines.extend(f"- {_operation_label(item)}" for item in changed)
    if unchanged:
        lines.append(f"- {len(unchanged)} item(s) already up to date.")

    init_db = payload.get("init_db")
    init_graph = payload.get("init_graph")
    init_production = payload.get("init_production")
    if init_db or init_graph or init_production:
        lines.append("")
        lines.append("Initialized:")
        if init_db:
            lines.append(f"- SQLite memory DB: {init_db.get('db_path', '')}")
        if init_graph:
            status = "ready" if init_graph.get("ok") else f"skipped: {init_graph.get('error', '')}"
            lines.append(f"- Kuzu graph: {status}")
        if init_production:
            if init_production.get("ok"):
                reason = init_production.get("reason", "ready")
                lines.append(f"- Production marker: {reason}")
            else:
                lines.append(f"- Production marker: skipped: {init_production.get('error', '')}")

    model_result = payload.get("models")
    if model_result:
        lines.append("")
        lines.append(f"Model cache: {'ready' if model_result.get('ok') else 'check required'}")

    lines.extend(
        [
            "",
            "Next:",
            "1. Restart Codex or Claude so hooks and MCP config reload.",
            "2. Run: amo-cli doctor --target codex",
            "3. Run: amo-daemon",
        ]
    )
    return "\n".join(lines)


def _operation_label(operation: dict) -> str:
    labels = {
        "amo": "AMO runtime config",
        "amo-hook-launcher": "hook launcher",
        "codex": "Codex MCP config",
        "codex-hooks": "Codex command hooks",
        "codex-skill-checkpoint": "Codex skill-checkpoint helper",
        "claude": "Claude MCP and hook config",
        "claude-skill-checkpoint": "Claude skill-checkpoint command",
    }
    return labels.get(str(operation.get("target", "")), str(operation.get("target", "config")))


def handle_install_command(
    args: Any,
    *,
    emit: Callable[[object], None],
    emit_text: Callable[[str], None],
) -> int | None:
    """Run installer, doctor, and uninstall CLI commands."""
    if args.command == "install":
        options = InstallOptions(
            target=args.target,
            user_home=args.user_home,
            amo_home=args.amo_home,
            python_command=args.python_command,
            preset=args.preset,
            embedding_model=args.embedding_model,
            reranker_model=args.reranker_model,
            qwen_model=args.qwen_model,
            force=args.force,
        )
        plan = build_install_plan(options)
        summary = summarize_install_plan(plan)
        if args.dry_run:
            if args.json:
                emit({"ok": True, "dry_run": True, "plan": summary})
            else:
                emit_text(format_install_plan(summary, dry_run=True))
            return 0
        if not args.yes:
            if args.json:
                emit({"ok": True, "pending_plan": summary})
            else:
                emit_text(format_install_plan(summary))
            if not confirm("Apply AMO install changes?"):
                if args.json:
                    emit({"ok": False, "cancelled": True, "plan": summary})
                else:
                    emit_text("Install cancelled. No files changed.")
                return 1
        result = apply_install_plan(plan)
        model_result = None
        if args.download_models:
            model_result = download_models(
                preset=args.preset,
                embedding_model=args.embedding_model,
                reranker_model=args.reranker_model,
                qwen_model=args.qwen_model,
            )
        init_result = None
        init_graph = None
        init_production = None
        if not args.skip_init_db:
            os.environ["AMO_HOME"] = plan["amo_home"]
            init_settings = Settings.load()
            svc = MemoryService(init_settings)
            try:
                svc.init_db()
                init_result = {"db_path": str(init_settings.db_path)}
            finally:
                svc.close()
            try:
                graph = GraphRagService(init_settings)
                graph.close()
                init_graph = {"ok": True, "graph_path": str(init_settings.graph_path)}
            except GraphBackendUnavailable as exc:
                init_graph = {"ok": False, "error": str(exc)}
            if init_graph.get("ok"):
                try:
                    init_production = initialize_fresh_production_storage(init_settings)
                except Exception as exc:
                    init_production = {
                        "ok": False,
                        "error": str(exc),
                        "note": "Existing non-empty graph/retrieval stores need explicit reset-production or adopt-production.",
                    }
        payload = {
            "ok": True,
            "plan": summary,
            "apply": result,
            "models": model_result,
            "init_db": init_result,
            "init_graph": init_graph,
            "init_production": init_production,
        }
        if args.json:
            emit(payload)
        else:
            emit_text(format_install_result(payload))
        return 0

    if args.command == "doctor":
        result = install_doctor(target=args.target, user_home=args.user_home, amo_home=args.amo_home)
        emit(result)
        return 0 if result["ok"] else 1

    if args.command == "uninstall":
        if not args.yes and not confirm("Remove AMO-managed config entries?"):
            emit({"ok": False, "cancelled": True})
            return 1
        emit(uninstall_targets(target=args.target, user_home=args.user_home))
        return 0

    return None


__all__ = [
    "INSTALL_COMMANDS",
    "add_install_subcommands",
    "add_model_selection_args",
    "codex_hooks_snippet",
    "confirm",
    "format_install_plan",
    "format_install_result",
    "handle_install_command",
    "summarize_install_plan",
]
