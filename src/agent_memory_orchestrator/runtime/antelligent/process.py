from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
from pathlib import Path
from typing import Any

from ...core.config import Settings
from .install import installed_executable, install_metadata
from .paths import is_windows, paths_for


def start_antelligent(settings: Settings) -> dict[str, Any]:
    exe = installed_executable(settings)
    if exe is None or not exe.exists():
        return {"ok": False, "error": "antelligent_not_installed", "hint": "Run: amo-cli antelligent install"}
    status = status_antelligent(settings)
    if status.get("running"):
        return {"ok": True, "already_running": True, "pid": status.get("pid"), "executable": str(exe)}
    env = os.environ.copy()
    paths = paths_for(settings)
    env["AMO_HOME"] = str(settings.home)
    env["ANTELLIGENT_CONFIG"] = str(paths.launch_config_path)
    kwargs: dict[str, Any] = {"cwd": str(exe.parent), "env": env}
    if is_windows():
        kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
    process = subprocess.Popen([str(exe)], **kwargs)  # noqa: S603 - executable is installed and verified by AMO.
    _write_pid(paths.pid_path, process.pid)
    return {"ok": True, "pid": process.pid, "executable": str(exe), "config": str(paths.launch_config_path)}


def stop_antelligent(settings: Settings) -> dict[str, Any]:
    paths = paths_for(settings)
    pid = _read_pid(paths.pid_path)
    if not pid:
        return {"ok": True, "running": False, "stale_pid_removed": False}
    if not _pid_exists(pid):
        _safe_unlink(paths.pid_path)
        return {"ok": True, "running": False, "stale_pid_removed": True, "pid": pid}
    if not _process_matches_antelligent(settings, pid):
        return {"ok": False, "error": "pid_does_not_match_antelligent", "pid": pid}
    if is_windows():
        result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False)
        ok = result.returncode == 0
    else:
        try:
            os.kill(pid, signal.SIGTERM)
            ok = True
            result = None
        except OSError:
            ok = False
            result = None
    if ok:
        _safe_unlink(paths.pid_path)
    payload: dict[str, Any] = {"ok": ok, "pid": pid, "running": not ok}
    if is_windows() and result is not None:
        payload["stdout"] = result.stdout.strip()
        payload["stderr"] = result.stderr.strip()
    return payload


def status_antelligent(settings: Settings) -> dict[str, Any]:
    paths = paths_for(settings)
    exe = installed_executable(settings)
    metadata = install_metadata(settings)
    pid = _read_pid(paths.pid_path)
    running = bool(pid and _pid_exists(pid) and _process_matches_antelligent(settings, pid))
    stale_pid = bool(pid and not running)
    return {
        "ok": True,
        "installed": bool(exe and exe.exists()),
        "running": running,
        "pid": pid if running else None,
        "stale_pid": stale_pid,
        "app_dir": str(paths.app_dir),
        "executable": str(exe) if exe else "",
        "install": metadata or {},
        "pid_path": str(paths.pid_path),
        "launch_config": str(paths.launch_config_path),
    }


def write_current_pid(settings: Settings) -> dict[str, Any]:
    path = paths_for(settings).pid_path
    _write_pid(path, os.getpid())
    return {"ok": True, "pid": os.getpid(), "path": str(path)}


def clear_current_pid(settings: Settings) -> dict[str, Any]:
    path = paths_for(settings).pid_path
    pid = _read_pid(path)
    if pid == os.getpid():
        _safe_unlink(path)
        return {"ok": True, "removed": True, "path": str(path)}
    return {"ok": True, "removed": False, "path": str(path)}


def _write_pid(path: Path, pid: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": int(pid)}) + "\n", encoding="utf-8")


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    return pid if pid > 0 else None


def _pid_exists(pid: int) -> bool:
    if is_windows():
        result = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], text=True, capture_output=True, check=False)
        return str(pid) in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _process_matches_antelligent(settings: Settings, pid: int) -> bool:
    exe = installed_executable(settings)
    if is_windows():
        expected = str(exe.resolve()).lower() if exe and exe.exists() else ""
        command = [
            "powershell",
            "-NoProfile",
            "-Command",
            f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue).Path",
        ]
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        actual = result.stdout.strip().lower()
        return bool(expected and actual and actual == expected)
    result = subprocess.run(["ps", "-p", str(pid), "-o", "command="], text=True, capture_output=True, check=False)
    command_line = result.stdout.strip()
    if not exe or not exe.exists() or not command_line:
        return False
    try:
        argv = shlex.split(command_line)
    except ValueError:
        return False
    if not argv:
        return False
    expected_path = exe.resolve()
    argv0 = Path(argv[0])
    if argv0.is_absolute():
        try:
            return argv0.resolve() == expected_path
        except OSError:
            return False
    return argv0.name.lower() == expected_path.name.lower()


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


__all__ = ["clear_current_pid", "start_antelligent", "status_antelligent", "stop_antelligent", "write_current_pid"]
