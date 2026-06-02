from __future__ import annotations

from typing import Any


def _is_amo_claude_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        if isinstance(hook, dict) and _is_amo_hook_command(str(hook.get("command", ""))):
            return True
    return False


def _is_amo_hook_command(command: str) -> bool:
    return (
        "agent_memory_orchestrator.hook" in command
        or "agent_memory_orchestrator.runtime.hook.launcher" in command
        or "amo_hook_launcher.py" in command
    )


def _is_amo_codex_hook(entry: Any) -> bool:
    return _is_amo_claude_hook(entry)


def _codex_has_amo_hooks(payload: dict[str, Any]) -> bool:
    hooks = payload.get("hooks", {})
    return any(_is_amo_codex_hook(entry) for entries in hooks.values() if isinstance(entries, list) for entry in entries)


def _claude_has_amo_hooks(payload: dict[str, Any]) -> bool:
    hooks = payload.get("hooks", {})
    return any(_is_amo_claude_hook(entry) for entries in hooks.values() if isinstance(entries, list) for entry in entries)
