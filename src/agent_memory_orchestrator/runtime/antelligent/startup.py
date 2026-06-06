from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path
from typing import Any

from ...core.config import Settings
from .install import installed_executable
from .paths import APP_NAME, is_macos, is_windows, paths_for

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCH_AGENT_LABEL = "com.agent-memory-orchestrator.antelligent"
LAUNCHCTL_TIMEOUT_SECONDS = 10


def install_startup(settings: Settings) -> dict[str, Any]:
    exe = installed_executable(settings)
    if exe is None or not exe.exists():
        return {"ok": False, "error": "antelligent_not_installed", "hint": "Run: amo-cli antelligent install"}
    if is_windows():
        return _install_windows_run_key(exe)
    if is_macos():
        return _install_macos_launch_agent(settings, exe)
    return {"ok": False, "error": "unsupported_platform", "platform": os.sys.platform}


def uninstall_startup(settings: Settings) -> dict[str, Any]:
    if is_windows():
        return _uninstall_windows_run_key()
    if is_macos():
        return _uninstall_macos_launch_agent()
    return {"ok": False, "error": "unsupported_platform", "platform": os.sys.platform}


def startup_status(settings: Settings) -> dict[str, Any]:
    if is_windows():
        value = _read_windows_run_key()
        return {"ok": True, "platform": "windows", "enabled": bool(value), "command": value or "", "token_free": "token" not in (value or "").lower()}
    if is_macos():
        path = _launch_agent_path()
        return {"ok": True, "platform": "darwin", "enabled": path.exists(), "plist_path": str(path), "token_free": _plist_token_free(path)}
    return {"ok": False, "error": "unsupported_platform", "platform": os.sys.platform}


def _install_windows_run_key(exe: Path) -> dict[str, Any]:
    import winreg

    command = windows_run_command(exe)
    if len(command) > 260:
        return {"ok": False, "error": "windows_run_command_too_long", "length": len(command), "command": command}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, command)
    return {"ok": True, "platform": "windows", "method": "hkcu-run", "command": command, "length": len(command), "token_free": True}


def _uninstall_windows_run_key() -> dict[str, Any]:
    import winreg

    removed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, APP_NAME)
                removed = True
            except FileNotFoundError:
                removed = False
    except FileNotFoundError:
        removed = False
    return {"ok": True, "platform": "windows", "method": "hkcu-run", "removed": removed}


def _read_windows_run_key() -> str:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, APP_NAME)
            return str(value)
    except FileNotFoundError:
        return ""


def _install_macos_launch_agent(settings: Settings, exe: Path) -> dict[str, Any]:
    path = _launch_agent_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    plist = macos_launch_agent_plist(settings, exe)
    with path.open("wb") as handle:
        plistlib.dump(plist, handle)
    bootout = _run_launchctl(["launchctl", "bootout", _launchd_domain(), str(path)], path)
    if isinstance(bootout, dict):
        return bootout
    result = _run_launchctl(["launchctl", "bootstrap", _launchd_domain(), str(path)], path)
    if isinstance(result, dict):
        return result
    return {
        "ok": result.returncode == 0,
        "platform": "darwin",
        "method": "launch-agent",
        "plist_path": str(path),
        "run_at_load": True,
        "keep_alive": False,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
        "token_free": True,
    }


def _uninstall_macos_launch_agent() -> dict[str, Any]:
    path = _launch_agent_path()
    result = _run_launchctl(["launchctl", "bootout", _launchd_domain(), str(path)], path)
    if isinstance(result, dict):
        return result
    removed = False
    if path.exists():
        path.unlink()
        removed = True
    return {
        "ok": True,
        "platform": "darwin",
        "method": "launch-agent",
        "plist_path": str(path),
        "removed": removed,
        "launchctl": {"returncode": result.returncode, "stdout": result.stdout.strip(), "stderr": result.stderr.strip()},
    }


def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"


def _run_launchctl(command: list[str], path: Path) -> subprocess.CompletedProcess[str] | dict[str, Any]:
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=LAUNCHCTL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "platform": "darwin",
            "method": "launch-agent",
            "plist_path": str(path),
            "error": "launchctl_timeout",
            "timeout_seconds": LAUNCHCTL_TIMEOUT_SECONDS,
        }


def windows_run_command(exe: Path) -> str:
    return f'"{exe.resolve()}"'


def macos_launch_agent_plist(settings: Settings, exe: Path) -> dict[str, Any]:
    launch_config = paths_for(settings).launch_config_path
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [str(exe.resolve())],
        "EnvironmentVariables": {
            "AMO_HOME": str(settings.home),
            "ANTELLIGENT_CONFIG": str(launch_config),
        },
        "RunAtLoad": True,
        "KeepAlive": False,
        "StandardOutPath": str(settings.home / "apps" / "antelligent" / "antelligent.out.log"),
        "StandardErrorPath": str(settings.home / "apps" / "antelligent" / "antelligent.err.log"),
    }


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _plist_token_free(path: Path) -> bool:
    if not path.exists():
        return True
    return "token" not in path.read_text(encoding="utf-8", errors="ignore").lower()


__all__ = [
    "install_startup",
    "macos_launch_agent_plist",
    "startup_status",
    "uninstall_startup",
    "windows_run_command",
]
