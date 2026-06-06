from __future__ import annotations

from typing import Any

from ...core.config import Settings
from .install import install_metadata, installed_executable
from .launch_config import read_launch_config
from .process import status_antelligent
from .startup import startup_status


def doctor(settings: Settings) -> dict[str, Any]:
    status = status_antelligent(settings)
    startup = startup_status(settings)
    launch = read_launch_config(settings)
    checks = [
        _check("installed", bool(status.get("installed")), "Run: amo-cli antelligent install"),
        _check("launch_config", bool(launch), "Launch config missing; reinstall or run: amo-cli antelligent install --force"),
        _check("daemon_command_absolute", _daemon_command_absolute(launch), "Launch config must use an absolute Python path."),
        _check("startup_token_free", bool(startup.get("token_free", True)), "Startup entry must not contain token values."),
        _check("pid_safe", not bool(status.get("stale_pid")), "Stale PID can be cleared with: amo-cli antelligent stop"),
    ]
    ok = all(item["ok"] for item in checks)
    return {
        "ok": ok,
        "status": status,
        "startup": startup,
        "install": install_metadata(settings) or {},
        "executable": str(installed_executable(settings) or ""),
        "checks": checks,
    }


def _daemon_command_absolute(launch: dict[str, Any] | None) -> bool:
    if not launch:
        return False
    command = launch.get("daemon_command") if isinstance(launch, dict) else None
    if not isinstance(command, dict):
        return False
    program = str(command.get("program") or "")
    return bool(program and program.lower() not in {"python", "python3", "amo-daemon"} and (":" in program or program.startswith("/")))


def _check(name: str, ok: bool, hint: str) -> dict[str, Any]:
    return {"name": name, "ok": ok, "hint": "" if ok else hint}


__all__ = ["doctor"]
