from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

from ...core.config import Settings

APP_NAME = "Antelligent"
WINDOWS_APP_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / "AgentMemoryOrchestrator" / APP_NAME
MACOS_APP_DIR = Path.home() / ".agent-memory-orchestrator" / "apps" / "antelligent"
MANAGED_DIR_NAME = "antelligent"


@dataclass(frozen=True, slots=True)
class AntelligentPaths:
    settings: Settings
    app_dir: Path
    metadata_dir: Path
    install_json: Path
    pid_path: Path
    launch_config_path: Path
    token_path: Path


def paths_for(settings: Settings) -> AntelligentPaths:
    app_dir = default_app_dir()
    metadata_dir = settings.home / "apps" / MANAGED_DIR_NAME
    return AntelligentPaths(
        settings=settings,
        app_dir=app_dir,
        metadata_dir=metadata_dir,
        install_json=metadata_dir / "install.json",
        pid_path=metadata_dir / "antelligent.pid",
        launch_config_path=settings.home / ".ui" / "antelligent.launch.json",
        token_path=settings.home / ".ui" / "antelligent.token",
    )


def default_app_dir() -> Path:
    if override := os.environ.get("ANTELLIGENT_APP_DIR"):
        return Path(override).expanduser().resolve()
    if is_windows():
        return WINDOWS_APP_DIR
    if is_macos():
        return MACOS_APP_DIR
    return Path.home() / ".agent-memory-orchestrator" / "apps" / "antelligent"


def platform_key() -> str:
    if is_windows():
        return "windows"
    if is_macos():
        return "darwin"
    return sys.platform


def arch_key() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine


def executable_name() -> str:
    return "antelligent.exe" if is_windows() else "antelligent"


def executable_names() -> tuple[str, ...]:
    if is_windows():
        return ("antelligent.exe", "Antelligent.exe")
    return ("antelligent", "Antelligent")


def is_windows() -> bool:
    return sys.platform.startswith("win")


def is_macos() -> bool:
    return sys.platform == "darwin"


__all__ = [
    "APP_NAME",
    "AntelligentPaths",
    "arch_key",
    "default_app_dir",
    "executable_name",
    "executable_names",
    "is_macos",
    "is_windows",
    "paths_for",
    "platform_key",
]
