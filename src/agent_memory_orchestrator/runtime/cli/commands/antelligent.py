from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ...antelligent import doctor as antelligent_doctor
from ...antelligent import install_antelligent, install_startup, start_antelligent, startup_status
from ...antelligent import status_antelligent, stop_antelligent, uninstall_antelligent, uninstall_startup, write_launch_config

ANTELLIGENT_COMMANDS = ("antelligent",)


def add_antelligent_subcommands(sub: Any) -> None:
    parser = sub.add_parser("antelligent", help="Manage the Antelligent floating desktop companion")
    ant_sub = parser.add_subparsers(dest="antelligent_command", required=True)

    install = ant_sub.add_parser("install", help="Install the Antelligent desktop app artifact")
    install.add_argument("--amo-home", type=Path, default=Path.home() / ".agent-memory-orchestrator")
    install.add_argument("--version", default="latest")
    install.add_argument("--artifact", type=Path, help="Install from a local zip/tar.gz artifact")
    install.add_argument("--manifest", help="Manifest URL or local manifest path")
    install.add_argument("--force", action="store_true")
    install.add_argument("--install-startup", action="store_true", help="Enable login startup after install")
    install.add_argument("--start", action="store_true", help="Start Antelligent after install")

    for name, help_text in [
        ("start", "Start Antelligent"),
        ("stop", "Stop Antelligent without stopping AMO daemon"),
        ("status", "Show Antelligent install/process/startup status"),
        ("doctor", "Diagnose Antelligent setup"),
        ("install-startup", "Enable Antelligent at user login"),
        ("uninstall-startup", "Disable Antelligent login startup"),
        ("uninstall", "Remove Antelligent app files"),
        ("write-launch-config", "Rewrite Antelligent daemon launch config"),
    ]:
        cmd = ant_sub.add_parser(name, help=help_text)
        cmd.add_argument("--amo-home", type=Path, default=Path.home() / ".agent-memory-orchestrator")
    ant_sub.choices["uninstall"].add_argument("--remove-config", action="store_true")


def handle_antelligent_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    if args.command != "antelligent":
        return None
    settings = _settings(args.amo_home)
    command = args.antelligent_command
    if command == "install":
        result = install_antelligent(
            settings,
            version=args.version,
            artifact_path=args.artifact,
            manifest=args.manifest,
            force=args.force,
            python_executable=sys.executable,
        )
        if args.install_startup:
            result["startup"] = install_startup(settings)
            if not result["startup"].get("ok"):
                result["ok"] = False
        if args.start:
            result["start"] = start_antelligent(settings)
            if not result["start"].get("ok"):
                result["ok"] = False
        emit(result)
        return 0 if result.get("ok") else 1
    if command == "start":
        result = start_antelligent(settings)
    elif command == "stop":
        result = stop_antelligent(settings)
    elif command == "status":
        result = status_antelligent(settings) | {"startup": startup_status(settings)}
    elif command == "doctor":
        result = antelligent_doctor(settings)
    elif command == "install-startup":
        result = install_startup(settings)
    elif command == "uninstall-startup":
        result = uninstall_startup(settings)
    elif command == "uninstall":
        uninstall_startup(settings)
        result = uninstall_antelligent(settings, remove_config=args.remove_config)
    elif command == "write-launch-config":
        result = write_launch_config(settings, python_executable=sys.executable)
    else:
        result = {"ok": False, "error": f"unknown antelligent command: {command}"}
    emit(result)
    return 0 if result.get("ok") else 1


def _settings(amo_home: Path) -> Settings:
    previous = os.environ.get("AMO_HOME")
    os.environ["AMO_HOME"] = str(amo_home)
    try:
        return Settings.load()
    finally:
        if previous is None:
            os.environ.pop("AMO_HOME", None)
        else:
            os.environ["AMO_HOME"] = previous


__all__ = ["ANTELLIGENT_COMMANDS", "add_antelligent_subcommands", "handle_antelligent_command"]
