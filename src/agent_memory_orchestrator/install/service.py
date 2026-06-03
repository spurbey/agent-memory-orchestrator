from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..infrastructure.llm import resolve_models
from .constants import CLAUDE_MCP_NAME
from .constants import CODEX_MCP_NAME
from .constants import MANAGED_BEGIN
from .constants import MANAGED_END
from .constants import SKILL_CHECKPOINT_MARKER
from .detection import _claude_has_amo_hooks
from .detection import _codex_has_amo_hooks
from .detection import _is_amo_claude_hook
from .detection import _is_amo_codex_hook
from .io import _backup_path
from .io import _read_json
from .io import _rstrip
from .io import _safe_read
from .targets import _expand_targets
from .targets import _normalize_target
from .templates import _claude_skill_checkpoint_command
from .templates import _config_operation
from .templates import _codex_managed_block
from .templates import _codex_skill_checkpoint_skill
from .templates import _hook_command
from .templates import _hook_events
from .templates import _hook_launcher_operation
from .templates import write_runtime_config

__all__ = [
    "InstallOptions",
    "apply_install_plan",
    "build_install_plan",
    "doctor",
    "uninstall",
    "write_runtime_config",
]


@dataclass(slots=True, frozen=True)
class InstallOptions:
    target: str = "all"
    user_home: Path = Path.home()
    amo_home: Path = Path.home() / ".agent-memory-orchestrator"
    preset: str = "cpu-balanced"
    embedding_model: str | None = None
    reranker_model: str | None = None
    qwen_model: str | None = None
    python_command: str = "python"
    force: bool = False


def build_install_plan(options: InstallOptions) -> dict[str, Any]:
    target = _normalize_target(options.target)
    targets = _expand_targets(target)
    resolved_models = resolve_models(
        preset=options.preset,
        embedding_model=options.embedding_model,
        reranker_model=options.reranker_model,
        qwen_model=options.qwen_model,
    )
    amo_home = options.amo_home.expanduser().resolve()
    user_home = options.user_home.expanduser().resolve()
    hook_launcher = amo_home / "bin" / "amo_hook_launcher.py"

    operations: list[dict[str, Any]] = [
        _config_operation(amo_home=amo_home, resolved_models=resolved_models),
        _hook_launcher_operation(launcher_path=hook_launcher),
    ]
    if "codex" in targets:
        operations.append(
            _codex_operation(
                config_path=user_home / ".codex" / "config.toml",
                amo_home=amo_home,
                python_command=options.python_command,
                force=options.force,
            )
        )
        operations.append(
            _codex_hooks_operation(
                hooks_path=user_home / ".codex" / "hooks.json",
                amo_home=amo_home,
                hook_launcher=hook_launcher,
                python_command=options.python_command,
            )
        )
        operations.append(
            _codex_skill_checkpoint_operation(
                skill_path=user_home / ".codex" / "skills" / "amo-skill-checkpoint" / "SKILL.md",
                amo_home=amo_home,
                python_command=options.python_command,
            )
        )
    if "claude" in targets:
        operations.append(
            _claude_operation(
                settings_path=user_home / ".claude" / "settings.json",
                amo_home=amo_home,
                python_command=options.python_command,
            )
        )
        operations.append(
            _claude_skill_checkpoint_command_operation(
                command_path=user_home / ".claude" / "commands" / "skill-checkpoint.md",
                amo_home=amo_home,
                python_command=options.python_command,
            )
        )

    return {
        "target": target,
        "targets": targets,
        "user_home": str(user_home),
        "amo_home": str(amo_home),
        "models": resolved_models,
        "operations": operations,
        "notes": [
            "No hosted model/API calls are configured by AMO.",
            "Agent configs are backed up before apply.",
            "Run uninstall to remove AMO-managed hooks/MCP entries.",
        ],
    }


def apply_install_plan(plan: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for operation in plan["operations"]:
        path = Path(operation["path"])
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        after = operation["after"]
        changed = before != after
        backup_path = ""
        if changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                backup = _backup_path(path)
                shutil.copy2(path, backup)
                backup_path = str(backup)
            path.write_text(after, encoding="utf-8")
        results.append(
            {
                "target": operation["target"],
                "path": str(path),
                "changed": changed,
                "backup_path": backup_path,
            }
        )
    return {"ok": True, "results": results}


def uninstall(target: str = "all", user_home: Path | None = None) -> dict[str, Any]:
    selected = _expand_targets(_normalize_target(target))
    home = (user_home or Path.home()).expanduser().resolve()
    results: list[dict[str, Any]] = []
    if "codex" in selected:
        results.append(_uninstall_codex(home / ".codex" / "config.toml"))
        results.append(_uninstall_codex_hooks(home / ".codex" / "hooks.json"))
        results.append(_uninstall_managed_file(home / ".codex" / "skills" / "amo-skill-checkpoint" / "SKILL.md", "codex-skill-checkpoint"))
    if "claude" in selected:
        path = home / ".claude" / "settings.json"
        results.append(_uninstall_claude(path))
        results.append(_uninstall_managed_file(home / ".claude" / "commands" / "skill-checkpoint.md", "claude-skill-checkpoint"))
    return {"ok": True, "results": results}


def doctor(target: str = "all", user_home: Path | None = None, amo_home: Path | None = None) -> dict[str, Any]:
    selected = _expand_targets(_normalize_target(target))
    home = (user_home or Path.home()).expanduser().resolve()
    actual_amo_home = (amo_home or home / ".agent-memory-orchestrator").expanduser().resolve()
    checks: dict[str, Any] = {
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "amo_home": {
            "path": str(actual_amo_home),
            "exists": actual_amo_home.exists(),
            "config_exists": (actual_amo_home / "config.json").exists(),
        },
    }
    if "codex" in selected:
        text = _safe_read(home / ".codex" / "config.toml")
        hooks_payload = _read_json(home / ".codex" / "hooks.json")
        checks["codex"] = {
            "config_path": str(home / ".codex" / "config.toml"),
            "hooks_path": str(home / ".codex" / "hooks.json"),
            "config_exists": bool(text),
            "hooks_file_exists": bool(hooks_payload),
            "managed_block": MANAGED_BEGIN in text and MANAGED_END in text,
            "mcp_configured": CODEX_MCP_NAME in text,
            "hooks_configured": (
                "agent_memory_orchestrator.hook" in text
                or "agent_memory_orchestrator.runtime.hook.launcher" in text
                or _codex_has_amo_hooks(hooks_payload)
            ),
            "codex_hooks_enabled": bool(re.search(r"(?m)^\s*codex_hooks\s*=\s*true\s*$", text)),
            "skill_checkpoint_configured": (home / ".codex" / "skills" / "amo-skill-checkpoint" / "SKILL.md").exists(),
        }
    if "claude" in selected:
        path = home / ".claude" / "settings.json"
        payload = _read_json(path)
        checks["claude"] = {
            "settings_path": str(path),
            "settings_exists": path.exists(),
            "mcp_configured": CLAUDE_MCP_NAME in payload.get("mcpServers", {}),
            "hooks_configured": _claude_has_amo_hooks(payload),
            "skill_checkpoint_configured": (home / ".claude" / "commands" / "skill-checkpoint.md").exists(),
        }
    ok = checks["amo_home"]["config_exists"] and all(
        not isinstance(item, dict)
        or item.get("mcp_configured", True)
        or item.get("executable")
        or item.get("path")
        for item in checks.values()
    )
    return {"ok": bool(ok), "checks": checks}


def _codex_operation(*, config_path: Path, amo_home: Path, python_command: str, force: bool) -> dict[str, Any]:
    before = _safe_read(config_path)
    if CODEX_MCP_NAME in before and MANAGED_BEGIN not in before and not force:
        raise RuntimeError(
            f"Codex config already contains {CODEX_MCP_NAME}; rerun with --force or edit manually: {config_path}"
        )
    base = _remove_managed_block(before)
    base = _ensure_toml_feature(base, "codex_hooks", "true")
    managed = _codex_managed_block(amo_home=amo_home, python_command=python_command)
    after = _rstrip(base) + "\n\n" + managed + "\n"
    return {
        "target": "codex",
        "path": str(config_path),
        "exists": config_path.exists(),
        "after": after,
        "description": "Configure Codex feature flag and AMO MCP server. Hooks are written to hooks.json.",
    }


def _codex_hooks_operation(
    *,
    hooks_path: Path,
    amo_home: Path,
    hook_launcher: Path,
    python_command: str,
) -> dict[str, Any]:
    exists = hooks_path.exists()
    payload = _read_json(hooks_path)
    payload.setdefault("hooks", {})
    hooks = payload["hooks"]
    if not isinstance(hooks, dict):
        raise ValueError(f"expected hooks object in {hooks_path}")
    for event, entries in list(hooks.items()):
        if isinstance(entries, list):
            hooks[event] = [entry for entry in entries if not _is_amo_codex_hook(entry)]
            if not hooks[event]:
                hooks.pop(event, None)

    for event, matcher, status in _hook_events():
        entry: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_command(python_command, "codex", amo_home, hook_launcher=hook_launcher),
                    "timeout": 30,
                    "statusMessage": status,
                }
            ]
        }
        if matcher:
            entry["matcher"] = matcher
        hooks.setdefault(event, []).append(entry)

    return {
        "target": "codex-hooks",
        "path": str(hooks_path),
        "exists": exists,
        "after": json.dumps(payload, indent=2, sort_keys=True) + "\n",
        "description": "Configure Codex command hooks in hooks.json.",
    }


def _codex_skill_checkpoint_operation(*, skill_path: Path, amo_home: Path, python_command: str) -> dict[str, Any]:
    return {
        "target": "codex-skill-checkpoint",
        "path": str(skill_path),
        "exists": skill_path.exists(),
        "after": _codex_skill_checkpoint_skill(amo_home=amo_home, python_command=python_command),
        "description": "Install the AMO skill-checkpoint trigger skill for Codex.",
    }


def _claude_skill_checkpoint_command_operation(*, command_path: Path, amo_home: Path, python_command: str) -> dict[str, Any]:
    return {
        "target": "claude-skill-checkpoint",
        "path": str(command_path),
        "exists": command_path.exists(),
        "after": _claude_skill_checkpoint_command(amo_home=amo_home, python_command=python_command),
        "description": "Install the /skill-checkpoint command for Claude Code.",
    }


def _codex_hooks_cleanup_operation(*, hooks_path: Path) -> dict[str, Any]:
    exists = hooks_path.exists()
    payload = _read_json(hooks_path)
    if payload:
        hooks = payload.get("hooks", {})
        if not isinstance(hooks, dict):
            raise ValueError(f"expected hooks object in {hooks_path}")
        for event, entries in list(hooks.items()):
            if isinstance(entries, list):
                hooks[event] = [entry for entry in entries if not _is_amo_codex_hook(entry)]
                if not hooks[event]:
                    hooks.pop(event, None)
        if not hooks:
            payload.pop("hooks", None)
    return {
        "target": "codex-hooks",
        "path": str(hooks_path),
        "exists": exists,
        "after": json.dumps(payload, indent=2, sort_keys=True) + "\n" if payload or exists else "",
        "description": "Remove stale AMO hooks.json entries before writing the current hook set.",
    }


def _claude_operation(*, settings_path: Path, amo_home: Path, python_command: str) -> dict[str, Any]:
    payload = _read_json(settings_path)
    payload.setdefault("mcpServers", {})
    payload["mcpServers"][CLAUDE_MCP_NAME] = {
        "command": python_command,
        "args": ["-m", "agent_memory_orchestrator.runtime.mcp.server", "--amo-home", str(amo_home)],
    }
    hooks = payload.setdefault("hooks", {})
    for event, matcher, status in _hook_events():
        entries = hooks.setdefault(event, [])
        entries[:] = [_entry for _entry in entries if not _is_amo_claude_hook(_entry)]
        item: dict[str, Any] = {
            "hooks": [
                {
                    "type": "command",
                    "command": _hook_command(python_command, "claude", amo_home),
                    "timeout": 30,
                    "statusMessage": status,
                }
            ]
        }
        if matcher:
            item["matcher"] = matcher
        entries.append(item)
    after = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return {
        "target": "claude",
        "path": str(settings_path),
        "exists": settings_path.exists(),
        "after": after,
        "description": "Configure Claude Code hooks and AMO MCP server.",
    }


def _ensure_toml_feature(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    start = next((idx for idx, line in enumerate(lines) if line.strip() == "[features]"), None)
    if start is None:
        prefix = _rstrip(text)
        return (prefix + "\n\n" if prefix else "") + f"[features]\n{key} = {value}\n"

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if re.match(r"^\s*\[.*\]\s*$", lines[idx]):
            end = idx
            break

    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*).*$")
    for idx in range(start + 1, end):
        if pattern.match(lines[idx]):
            lines[idx] = pattern.sub(rf"\g<1>{value}", lines[idx])
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    lines.insert(end, f"{key} = {value}")
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def _remove_managed_block(text: str) -> str:
    pattern = re.compile(
        rf"(?ms)^\s*{re.escape(MANAGED_BEGIN)}.*?^\s*{re.escape(MANAGED_END)}\s*",
    )
    return pattern.sub("", text)


def _uninstall_codex(path: Path) -> dict[str, Any]:
    before = _safe_read(path)
    after = _remove_managed_block(before)
    changed = before != after
    backup_path = ""
    if changed:
        backup = _backup_path(path)
        shutil.copy2(path, backup)
        backup_path = str(backup)
        path.write_text(after, encoding="utf-8")
    return {
        "target": "codex",
        "path": str(path),
        "changed": changed,
        "backup_path": backup_path,
        "note": "codex_hooks feature is intentionally left unchanged if it was merged into an existing [features] table.",
    }


def _uninstall_codex_hooks(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    before = json.dumps(payload, indent=2, sort_keys=True) + "\n" if payload else ""
    hooks = payload.get("hooks", {})
    if isinstance(hooks, dict):
        for event, entries in list(hooks.items()):
            if isinstance(entries, list):
                hooks[event] = [entry for entry in entries if not _is_amo_codex_hook(entry)]
                if not hooks[event]:
                    hooks.pop(event, None)
        if not hooks:
            payload.pop("hooks", None)
    after = json.dumps(payload, indent=2, sort_keys=True) + "\n" if payload else ""
    changed = before != after
    backup_path = ""
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _backup_path(path)
            shutil.copy2(path, backup)
            backup_path = str(backup)
        path.write_text(after, encoding="utf-8")
    return {"target": "codex-hooks", "path": str(path), "changed": changed, "backup_path": backup_path}


def _uninstall_managed_file(path: Path, target: str) -> dict[str, Any]:
    changed = False
    backup_path = ""
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if SKILL_CHECKPOINT_MARKER in text:
            backup = _backup_path(path)
            shutil.copy2(path, backup)
            backup_path = str(backup)
            path.unlink()
            changed = True
    return {"target": target, "path": str(path), "changed": changed, "backup_path": backup_path}


def _uninstall_claude(path: Path) -> dict[str, Any]:
    payload = _read_json(path)
    before = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    payload.get("mcpServers", {}).pop(CLAUDE_MCP_NAME, None)
    hooks = payload.get("hooks", {})
    for event, entries in list(hooks.items()):
        if isinstance(entries, list):
            hooks[event] = [entry for entry in entries if not _is_amo_claude_hook(entry)]
            if not hooks[event]:
                hooks.pop(event, None)
    after = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    changed = before != after
    backup_path = ""
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = _backup_path(path)
            shutil.copy2(path, backup)
            backup_path = str(backup)
        path.write_text(after, encoding="utf-8")
    return {"target": "claude", "path": str(path), "changed": changed, "backup_path": backup_path}


