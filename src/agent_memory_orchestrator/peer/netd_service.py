from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_runtime import PeerNetdLaunchOptions


@dataclass(slots=True, frozen=True)
class PeerNetdServiceOptions:
    service_name: str = "AMO Peer Netd"
    apply: bool = False


def install_service_plan(
    settings: Settings,
    launch: PeerNetdLaunchOptions,
    options: PeerNetdServiceOptions,
) -> dict[str, Any]:
    enable_command = _enable_command(settings, launch)
    if os.name == "nt":
        task_name = options.service_name
        install_command = [
            "schtasks",
            "/Create",
            "/TN",
            task_name,
            "/SC",
            "ONLOGON",
            "/TR",
            subprocess.list2cmdline(enable_command),
            "/F",
        ]
        return {
            "ok": True,
            "platform": "windows",
            "service_name": task_name,
            "apply": options.apply,
            "enable_command": enable_command,
            "install_command": install_command,
            "uninstall_command": ["schtasks", "/Delete", "/TN", task_name, "/F"],
            "notes": [
                "Creates a per-user scheduled task that runs at logon.",
                "If --shared-secret-env is used, make sure that environment variable exists persistently for the user.",
            ],
        }

    unit_name = _systemd_unit_name(options.service_name)
    unit_path = Path.home() / ".config" / "systemd" / "user" / unit_name
    unit = _systemd_unit(unit_name, settings, enable_command)
    return {
        "ok": True,
        "platform": "systemd-user",
        "service_name": unit_name,
        "apply": options.apply,
        "enable_command": enable_command,
        "unit_path": str(unit_path),
        "unit": unit,
        "install_command": ["systemctl", "--user", "enable", "--now", unit_name],
        "uninstall_command": ["systemctl", "--user", "disable", "--now", unit_name],
        "notes": ["Writes a user systemd unit; no root service is required."],
    }


def install_service(settings: Settings, launch: PeerNetdLaunchOptions, options: PeerNetdServiceOptions) -> dict[str, Any]:
    plan = install_service_plan(settings, launch, options)
    if not options.apply:
        return plan
    if os.name == "nt":
        result = subprocess.run(plan["install_command"], text=True, capture_output=True, check=False)
        return plan | _completed_process_result(result)

    unit_path = Path(plan["unit_path"])
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(str(plan["unit"]), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], text=True, capture_output=True, check=False)
    result = subprocess.run(plan["install_command"], text=True, capture_output=True, check=False)
    return plan | _completed_process_result(result)


def uninstall_service(settings: Settings, options: PeerNetdServiceOptions) -> dict[str, Any]:
    plan = install_service_plan(settings, PeerNetdLaunchOptions(), options)
    command = plan["uninstall_command"]
    if not options.apply:
        return {
            "ok": True,
            "platform": plan["platform"],
            "service_name": plan["service_name"],
            "apply": False,
            "uninstall_command": command,
        }
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {
        "platform": plan["platform"],
        "service_name": plan["service_name"],
        "apply": True,
        "uninstall_command": command,
    } | _completed_process_result(result)


def service_status(options: PeerNetdServiceOptions) -> dict[str, Any]:
    if os.name == "nt":
        command = ["schtasks", "/Query", "/TN", options.service_name]
    else:
        command = ["systemctl", "--user", "status", _systemd_unit_name(options.service_name), "--no-pager"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {"service_name": options.service_name, "command": command} | _completed_process_result(result)


def _enable_command(settings: Settings, launch: PeerNetdLaunchOptions) -> list[str]:
    command = [
        sys.executable or "python",
        "-m",
        "agent_memory_orchestrator.app.cli",
        "peer",
        "--amo-home",
        str(settings.home),
        "enable",
        "--node-id",
        launch.node_id,
        "--listen",
        launch.listen_addr,
        "--api",
        launch.api_addr,
        "--mdns-service",
        launch.mdns_service,
    ]
    if launch.shared_secret_env:
        command.extend(["--shared-secret-env", launch.shared_secret_env])
    if launch.require_signature:
        command.append("--require-signature")
    for addr in launch.bootstrap_addrs:
        command.extend(["--bootstrap", addr])
    for addr in launch.static_relays:
        command.extend(["--static-relay", addr])
    for flag, enabled in [
        ("--mdns", launch.mdns),
        ("--rendezvous-server", launch.rendezvous_server),
        ("--relay-service", launch.relay_service),
        ("--nat-service", launch.nat_service),
        ("--auto-relay", launch.auto_relay),
        ("--hole-punching", launch.hole_punching),
        ("--force-private", launch.force_private),
        ("--force-public", launch.force_public),
        ("--advertise-localhost-dns", launch.advertise_localhost_dns),
    ]:
        if enabled:
            command.append(flag)
    return command


def _systemd_unit_name(service_name: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in service_name).strip("-")
    return (safe or "amo-peer-netd") + ".service"


def _systemd_unit(unit_name: str, settings: Settings, command: list[str]) -> str:
    return "\n".join(
        [
            "[Unit]",
            "Description=Agent Memory Orchestrator peer netd",
            "",
            "[Service]",
            "Type=simple",
            f"WorkingDirectory={settings.home}",
            "ExecStart=" + " ".join(_quote_systemd_arg(item) for item in command),
            "Restart=on-failure",
            "RestartSec=5",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )


def _quote_systemd_arg(value: str) -> str:
    if not value or any(ch.isspace() for ch in value):
        return "'" + value.replace("'", "'\\''") + "'"
    return value


def _completed_process_result(result: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
