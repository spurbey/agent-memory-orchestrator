from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ....core.privacy import redact_secrets


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


__all__ = [
    "add_model_selection_args",
    "codex_hooks_snippet",
    "confirm",
    "format_install_plan",
    "format_install_result",
    "summarize_install_plan",
]
