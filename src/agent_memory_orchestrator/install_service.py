from __future__ import annotations

import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model_manager import resolve_models


MANAGED_BEGIN = "# BEGIN AMO MANAGED BLOCK"
MANAGED_END = "# END AMO MANAGED BLOCK"
CLAUDE_MCP_NAME = "agent-memory-orchestrator"
CODEX_MCP_NAME = "agent_memory_orchestrator"
SUPPORTED_TARGETS = {"codex", "claude", "all"}


@dataclass(slots=True, frozen=True)
class InstallOptions:
    target: str = "all"
    user_home: Path = Path.home()
    amo_home: Path = Path.home() / ".agent-memory-orchestrator"
    preset: str = "cpu-balanced"
    embedding_model: str | None = None
    reranker_model: str | None = None
    python_command: str = "python"
    force: bool = False


def build_install_plan(options: InstallOptions) -> dict[str, Any]:
    target = _normalize_target(options.target)
    targets = _expand_targets(target)
    resolved_models = resolve_models(
        preset=options.preset,
        embedding_model=options.embedding_model,
        reranker_model=options.reranker_model,
    )
    amo_home = options.amo_home.expanduser().resolve()
    user_home = options.user_home.expanduser().resolve()

    operations: list[dict[str, Any]] = [
        _config_operation(amo_home=amo_home, resolved_models=resolved_models),
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
    if "claude" in targets:
        operations.append(
            _claude_operation(
                settings_path=user_home / ".claude" / "settings.json",
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
        path = home / ".codex" / "config.toml"
        results.append(_uninstall_codex(path))
    if "claude" in selected:
        path = home / ".claude" / "settings.json"
        results.append(_uninstall_claude(path))
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
        checks["codex"] = {
            "config_path": str(home / ".codex" / "config.toml"),
            "config_exists": bool(text),
            "managed_block": MANAGED_BEGIN in text and MANAGED_END in text,
            "mcp_configured": CODEX_MCP_NAME in text,
            "hooks_configured": "agent_memory_orchestrator.hook" in text,
            "codex_hooks_enabled": bool(re.search(r"(?m)^\s*codex_hooks\s*=\s*true\s*$", text)),
        }
    if "claude" in selected:
        path = home / ".claude" / "settings.json"
        payload = _read_json(path)
        checks["claude"] = {
            "settings_path": str(path),
            "settings_exists": path.exists(),
            "mcp_configured": CLAUDE_MCP_NAME in payload.get("mcpServers", {}),
            "hooks_configured": _claude_has_amo_hooks(payload),
        }
    ok = checks["amo_home"]["config_exists"] and all(
        not isinstance(item, dict)
        or item.get("mcp_configured", True)
        or item.get("executable")
        or item.get("path")
        for item in checks.values()
    )
    return {"ok": bool(ok), "checks": checks}


def write_runtime_config(amo_home: Path, resolved_models: dict[str, str]) -> str:
    payload = _runtime_config_payload(resolved_models)
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _config_operation(*, amo_home: Path, resolved_models: dict[str, str]) -> dict[str, Any]:
    path = amo_home / "config.json"
    return {
        "target": "amo",
        "path": str(path),
        "exists": path.exists(),
        "after": write_runtime_config(amo_home, resolved_models),
        "description": "Persist AMO runtime/model configuration.",
    }


def _runtime_config_payload(resolved_models: dict[str, str]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "local_only": True,
        "db_path": ".data/codex_live_memory.db",
        "export_dir": "exports",
        "approval_mode": "manual",
        "embedding_model": resolved_models["embedding_model"],
        "embedding_dims": 1024 if resolved_models["embedding_model"] == "BAAI/bge-m3" else 384,
        "reranker_backend": "cross-encoder",
        "reranker_model": resolved_models["reranker_model"],
        "vector_backend": resolved_models["vector_backend"],
        "model_preset": resolved_models["preset"],
    }


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
        "description": "Configure Codex hooks and AMO MCP server.",
    }


def _claude_operation(*, settings_path: Path, amo_home: Path, python_command: str) -> dict[str, Any]:
    payload = _read_json(settings_path)
    payload.setdefault("mcpServers", {})
    payload["mcpServers"][CLAUDE_MCP_NAME] = {
        "command": python_command,
        "args": ["-m", "agent_memory_orchestrator.mcp_server", "--amo-home", str(amo_home)],
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


def _codex_managed_block(*, amo_home: Path, python_command: str) -> str:
    mcp_args = ["-m", "agent_memory_orchestrator.mcp_server", "--amo-home", str(amo_home)]
    lines = [
        MANAGED_BEGIN,
        "# Managed by Agent Memory Orchestrator. Do not edit inside this block.",
        f"[mcp_servers.{CODEX_MCP_NAME}]",
        f"command = {_toml_string(python_command)}",
        f"args = {_toml_array(mcp_args)}",
        "",
    ]
    for event, matcher, status in _hook_events():
        lines.append(f"[[hooks.{event}]]")
        if matcher:
            lines.append(f"matcher = {_toml_string(matcher)}")
        lines.append(f"[[hooks.{event}.hooks]]")
        lines.append('type = "command"')
        lines.append(f"command = {_toml_string(_hook_command(python_command, 'codex', amo_home))}")
        lines.append("timeout = 30")
        lines.append(f"statusMessage = {_toml_string(status)}")
        lines.append("")
    lines.append(MANAGED_END)
    return "\n".join(lines)


def _hook_events() -> list[tuple[str, str, str]]:
    return [
        ("SessionStart", "startup|resume|clear", "AMO loading local memory context"),
        ("UserPromptSubmit", "", "AMO retrieving local memory"),
        ("PostToolUse", "*", "AMO capturing tool result"),
        ("Stop", "", "AMO summarizing session"),
    ]


def _hook_command(python_command: str, agent: str, amo_home: Path) -> str:
    return f'{python_command} -m agent_memory_orchestrator.hook --agent {agent} --amo-home "{amo_home}"'


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


def _is_amo_claude_hook(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    for hook in entry.get("hooks", []):
        if isinstance(hook, dict) and "agent_memory_orchestrator.hook" in str(hook.get("command", "")):
            return True
    return False


def _claude_has_amo_hooks(payload: dict[str, Any]) -> bool:
    hooks = payload.get("hooks", {})
    return any(_is_amo_claude_hook(entry) for entries in hooks.values() if isinstance(entries, list) for entry in entries)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _safe_read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _backup_path(path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    return path.with_name(f"{path.name}.amo-backup-{stamp}")


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _rstrip(text: str) -> str:
    return text.rstrip()


def _normalize_target(target: str) -> str:
    selected = target.strip().lower()
    if selected not in SUPPORTED_TARGETS:
        raise ValueError(f"target must be one of: {', '.join(sorted(SUPPORTED_TARGETS))}")
    return selected


def _expand_targets(target: str) -> list[str]:
    return ["codex", "claude"] if target == "all" else [target]
