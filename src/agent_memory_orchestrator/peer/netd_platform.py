from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path
from typing import Any


def binary_name() -> str:
    return "amo-peer-netd.exe" if os.name == "nt" else "amo-peer-netd"


def platform_binary_dir_name() -> str:
    system = platform.system().lower()
    if system.startswith("windows"):
        goos = "windows"
    elif system == "darwin":
        goos = "darwin"
    elif system == "linux":
        goos = "linux"
    else:
        goos = system or "unknown"

    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        goarch = "amd64"
    elif machine in {"arm64", "aarch64"}:
        goarch = "arm64"
    elif machine in {"armv7l", "armv7"}:
        goarch = "arm"
    elif machine in {"i386", "i686", "x86"}:
        goarch = "386"
    else:
        goarch = machine or "unknown"
    return f"{goos}-{goarch}"


def _missing_binary_requirements(capabilities: dict[str, Any]) -> list[str]:
    missing = [str(item) for item in capabilities.get("missing_required_flags", [])]
    missing.extend(f"protocol:{item}" for item in capabilities.get("missing_protocol_capabilities", []))
    return missing


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    flags = 0
    for name in ("CREATE_NO_WINDOW", "CREATE_NEW_PROCESS_GROUP", "DETACHED_PROCESS"):
        flags |= int(getattr(subprocess, name, 0))
    return flags


def _tail_text(path: Path, limit: int = 2000) -> str:
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]
