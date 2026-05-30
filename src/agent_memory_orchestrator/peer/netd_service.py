from __future__ import annotations

import os
import plistlib
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
    with_watcher: bool = False
    watch_service_name: str = ""


def install_service_plan(
    settings: Settings,
    launch: PeerNetdLaunchOptions,
    options: PeerNetdServiceOptions,
) -> dict[str, Any]:
    enable_command = _enable_command(settings, launch)
    watch_command = _watch_command(settings)
    if _is_windows():
        task_name = options.service_name
        script_path = _windows_script_path(settings, task_name)
        plan = {
            "ok": True,
            "platform": "windows",
            "preferred_startup_method": "scheduled-task",
            "service_name": task_name,
            "apply": options.apply,
            "enable_command": enable_command,
            "script_path": str(script_path),
            "script": _windows_cmd_script(settings, enable_command),
            "install_command": _windows_task_create_command(task_name, [str(script_path)]),
            "startup_launcher_path": str(_windows_startup_launcher_path(task_name)),
            "startup_launcher": _windows_vbs_launcher(script_path),
            "uninstall_command": ["schtasks", "/Delete", "/TN", task_name, "/F"],
            "notes": [
                "Creates a per-user scheduled task that runs at logon.",
                "Falls back to a per-user Startup folder launcher when Scheduled Tasks are blocked.",
                "Writes a short wrapper script so Windows Scheduled Tasks do not hit the /TR length limit.",
                "If --shared-secret-env is used, make sure that environment variable exists persistently for the user.",
            ],
        }
        if options.with_watcher:
            watcher_name = options.watch_service_name or f"{task_name} Watcher"
            watcher_script_path = _windows_script_path(settings, watcher_name)
            plan["watcher"] = {
                "service_name": watcher_name,
                "watch_command": watch_command,
                "script_path": str(watcher_script_path),
                "script": _windows_cmd_script(settings, watch_command),
                "install_command": _windows_task_create_command(watcher_name, [str(watcher_script_path)]),
                "startup_launcher_path": str(_windows_startup_launcher_path(watcher_name)),
                "startup_launcher": _windows_vbs_launcher(watcher_script_path),
                "start_command": ["schtasks", "/Run", "/TN", watcher_name],
                "fallback_start_command": ["wscript.exe", str(_windows_startup_launcher_path(watcher_name))],
                "uninstall_command": ["schtasks", "/Delete", "/TN", watcher_name, "/F"],
            }
            plan["notes"].append("Also creates a watcher task that drains peer-netd inbox messages into AMO rooms.")
        return plan

    if _is_macos():
        label = _launchd_label(options.service_name)
        plist_path = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        plan = {
            "ok": True,
            "platform": "launchd-user",
            "service_name": label,
            "apply": options.apply,
            "enable_command": enable_command,
            "plist_path": str(plist_path),
            "plist": _launchd_plist(settings, label, enable_command, "AMO peer netd"),
            "install_command": ["launchctl", "bootstrap", _launchd_domain(), str(plist_path)],
            "start_command": ["launchctl", "kickstart", "-k", f"{_launchd_domain()}/{label}"],
            "uninstall_command": ["launchctl", "bootout", _launchd_domain(), str(plist_path)],
            "notes": [
                "Writes a per-user LaunchAgent that runs at login and restarts on failure.",
                "If --shared-secret-env is used, make sure that environment variable exists persistently for the user.",
            ],
        }
        if options.with_watcher:
            watcher_label = _launchd_label(options.watch_service_name or "AMO Peer Netd Watcher")
            watcher_path = Path.home() / "Library" / "LaunchAgents" / f"{watcher_label}.plist"
            plan["watcher"] = {
                "service_name": watcher_label,
                "watch_command": watch_command,
                "plist_path": str(watcher_path),
                "plist": _launchd_plist(settings, watcher_label, watch_command, "AMO peer-agent watcher"),
                "install_command": ["launchctl", "bootstrap", _launchd_domain(), str(watcher_path)],
                "start_command": ["launchctl", "kickstart", "-k", f"{_launchd_domain()}/{watcher_label}"],
                "uninstall_command": ["launchctl", "bootout", _launchd_domain(), str(watcher_path)],
            }
            plan["notes"].append("Also writes a watcher LaunchAgent that lets bots answer peer requests unattended.")
        return plan

    unit_name = _systemd_unit_name(options.service_name)
    unit_path = Path.home() / ".config" / "systemd" / "user" / unit_name
    unit = _systemd_unit(unit_name, settings, enable_command)
    plan = {
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
    if options.with_watcher:
        watcher_name = _systemd_unit_name(options.watch_service_name or "AMO Peer Netd Watcher")
        watcher_path = Path.home() / ".config" / "systemd" / "user" / watcher_name
        plan["watcher"] = {
            "service_name": watcher_name,
            "watch_command": watch_command,
            "unit_path": str(watcher_path),
            "unit": _systemd_unit(watcher_name, settings, watch_command, description="AMO peer netd inbox watcher"),
            "install_command": ["systemctl", "--user", "enable", "--now", watcher_name],
            "uninstall_command": ["systemctl", "--user", "disable", "--now", watcher_name],
        }
        plan["notes"].append("Also writes a watcher unit that drains peer-netd inbox messages into AMO rooms.")
    return plan


def install_service(settings: Settings, launch: PeerNetdLaunchOptions, options: PeerNetdServiceOptions) -> dict[str, Any]:
    plan = install_service_plan(settings, launch, options)
    if not options.apply:
        return plan
    if _is_windows():
        _write_windows_script(Path(str(plan["script_path"])), str(plan["script"]))
        watcher = plan.get("watcher")
        if isinstance(watcher, dict):
            _write_windows_script(Path(str(watcher["script_path"])), str(watcher["script"]))
        commands = [plan["install_command"]]
        if isinstance(watcher, dict):
            commands.append(watcher["install_command"])
            commands.append(watcher["start_command"])
        scheduled = _run_commands(commands)
        if scheduled["ok"]:
            return plan | scheduled | {"startup_method": "scheduled-task"}
        fallback = _install_windows_startup_fallback(plan)
        return plan | fallback | {"scheduled_task_attempt": scheduled, "startup_method": "startup-folder"}

    if _is_macos():
        plist_path = Path(str(plan["plist_path"]))
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        _launchd_log_dir(settings).mkdir(parents=True, exist_ok=True)
        plist_path.write_text(str(plan["plist"]), encoding="utf-8")
        watcher = plan.get("watcher")
        preflight_commands = [plan["uninstall_command"]]
        commands = [plan["install_command"], plan["start_command"]]
        if isinstance(watcher, dict):
            watcher_path = Path(str(watcher["plist_path"]))
            watcher_path.parent.mkdir(parents=True, exist_ok=True)
            watcher_path.write_text(str(watcher["plist"]), encoding="utf-8")
            preflight_commands.append(watcher["uninstall_command"])
            commands.extend([watcher["install_command"], watcher["start_command"]])
        preflight_results = _run_commands(preflight_commands, ignore_failures=True)
        return plan | _run_commands(commands) | {"preflight": preflight_results}

    unit_path = Path(plan["unit_path"])
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(str(plan["unit"]), encoding="utf-8")
    watcher = plan.get("watcher")
    if isinstance(watcher, dict):
        watcher_path = Path(str(watcher["unit_path"]))
        watcher_path.parent.mkdir(parents=True, exist_ok=True)
        watcher_path.write_text(str(watcher["unit"]), encoding="utf-8")
    subprocess.run(["systemctl", "--user", "daemon-reload"], text=True, capture_output=True, check=False)
    commands = [plan["install_command"]]
    if isinstance(watcher, dict):
        commands.append(watcher["install_command"])
    return plan | _run_commands(commands)


def uninstall_service(settings: Settings, options: PeerNetdServiceOptions) -> dict[str, Any]:
    plan = install_service_plan(settings, PeerNetdLaunchOptions(), options)
    commands = [plan["uninstall_command"]]
    watcher = plan.get("watcher")
    if isinstance(watcher, dict):
        commands.append(watcher["uninstall_command"])
    if not options.apply:
        return {
            "ok": True,
            "platform": plan["platform"],
            "service_name": plan["service_name"],
            "apply": False,
            "uninstall_command": commands[0],
            "watcher": watcher,
        }
    result = {
        "platform": plan["platform"],
        "service_name": plan["service_name"],
        "apply": True,
        "uninstall_command": commands[0],
        "watcher": watcher,
    } | _run_commands(commands, ignore_failures=_is_windows())
    if _is_windows():
        result["startup_launcher_cleanup"] = _remove_windows_startup_launchers(plan)
        result["ok"] = bool(result["ok"] and result["startup_launcher_cleanup"]["ok"])
    return result


def service_status(options: PeerNetdServiceOptions) -> dict[str, Any]:
    if _is_windows():
        command = ["schtasks", "/Query", "/TN", options.service_name]
    elif _is_macos():
        command = ["launchctl", "print", f"{_launchd_domain()}/{_launchd_label(options.service_name)}"]
    else:
        command = ["systemctl", "--user", "status", _systemd_unit_name(options.service_name), "--no-pager"]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    payload: dict[str, Any] = {"service_name": options.service_name, "command": command} | _completed_process_result(result)
    if _is_windows():
        launcher_path = _windows_startup_launcher_path(options.service_name)
        payload["startup_launcher"] = {"path": str(launcher_path), "exists": launcher_path.exists()}
        payload["ok"] = bool(payload["ok"] or launcher_path.exists())
    if options.with_watcher:
        watch_name = options.watch_service_name or f"{options.service_name} Watcher"
        if _is_windows():
            watch_command = ["schtasks", "/Query", "/TN", watch_name]
        elif _is_macos():
            watch_command = ["launchctl", "print", f"{_launchd_domain()}/{_launchd_label(watch_name)}"]
        else:
            watch_command = ["systemctl", "--user", "status", _systemd_unit_name(watch_name), "--no-pager"]
        watch_result = subprocess.run(watch_command, text=True, capture_output=True, check=False)
        payload["watcher"] = {"service_name": watch_name, "command": watch_command} | _completed_process_result(
            watch_result
        )
        if _is_windows():
            watch_launcher_path = _windows_startup_launcher_path(watch_name)
            payload["watcher"]["startup_launcher"] = {"path": str(watch_launcher_path), "exists": watch_launcher_path.exists()}
            payload["watcher"]["ok"] = bool(payload["watcher"]["ok"] or watch_launcher_path.exists())
    return payload


def _windows_task_create_command(task_name: str, command: list[str]) -> list[str]:
    return [
        "schtasks",
        "/Create",
        "/TN",
        task_name,
        "/SC",
        "ONLOGON",
        "/TR",
        subprocess.list2cmdline(command),
        "/RL",
        "LIMITED",
        "/F",
    ]


def _windows_script_path(settings: Settings, service_name: str) -> Path:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in service_name).strip("-") or "amo-peer-netd"
    return settings.home / ".peer" / "service" / f"{safe}.cmd"


def _windows_startup_launcher_path(service_name: str) -> Path:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in service_name).strip("-") or "amo-peer-netd"
    startup_root = os.environ.get("APPDATA")
    if startup_root:
        base = Path(startup_root) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    else:
        base = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return base / f"{safe}.vbs"


def _windows_cmd_script(settings: Settings, command: list[str]) -> str:
    return "\r\n".join(
        [
            "@echo off",
            f"set AMO_HOME={settings.home}",
            f"cd /d {subprocess.list2cmdline([str(settings.home)])}",
            subprocess.list2cmdline(command),
            "",
        ]
    )


def _write_windows_script(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\r\n")


def _windows_vbs_launcher(script_path: Path) -> str:
    escaped = str(script_path).replace('"', '""')
    return "\r\n".join(
        [
            'Set WshShell = CreateObject("WScript.Shell")',
            f'WshShell.Run """{escaped}""", 0, False',
            "",
        ]
    )


def _install_windows_startup_fallback(plan: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    ok = True
    for entry in _windows_startup_entries(plan):
        launcher_path = Path(str(entry["startup_launcher_path"]))
        try:
            launcher_path.parent.mkdir(parents=True, exist_ok=True)
            launcher_path.write_text(str(entry["startup_launcher"]), encoding="utf-8", newline="\r\n")
            results.append({"ok": True, "action": "write_startup_launcher", "path": str(launcher_path)})
        except OSError as exc:
            ok = False
            results.append(
                {
                    "ok": False,
                    "action": "write_startup_launcher",
                    "path": str(launcher_path),
                    "error": str(exc),
                }
            )
    watcher = plan.get("watcher")
    if ok and isinstance(watcher, dict):
        start_command = list(watcher.get("fallback_start_command") or [])
        if start_command:
            result = subprocess.run(start_command, text=True, capture_output=True, check=False)
            completed = {"command": start_command} | _completed_process_result(result)
            results.append(completed)
            ok = ok and bool(completed["ok"])
    return {"ok": ok, "results": results}


def _windows_startup_entries(plan: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [plan]
    watcher = plan.get("watcher")
    if isinstance(watcher, dict):
        entries.append(watcher)
    return entries


def _remove_windows_startup_launchers(plan: dict[str, Any]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    ok = True
    for entry in _windows_startup_entries(plan):
        launcher_path = Path(str(entry.get("startup_launcher_path") or _windows_startup_launcher_path(entry["service_name"])))
        try:
            if launcher_path.exists():
                launcher_path.unlink()
                removed = True
            else:
                removed = False
            results.append({"ok": True, "action": "remove_startup_launcher", "path": str(launcher_path), "removed": removed})
        except OSError as exc:
            ok = False
            results.append(
                {
                    "ok": False,
                    "action": "remove_startup_launcher",
                    "path": str(launcher_path),
                    "error": str(exc),
                }
            )
    return {"ok": ok, "results": results}


def _is_windows() -> bool:
    return os.name == "nt"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _enable_command(settings: Settings, launch: PeerNetdLaunchOptions) -> list[str]:
    command = [
        sys.executable or "python",
        "-m",
        "agent_memory_orchestrator.runtime.cli.main",
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
    if launch.store_path:
        command.extend(["--store-path", launch.store_path])
    if launch.identity_key_path:
        command.extend(["--identity-key", launch.identity_key_path])
    if launch.shared_secret_env:
        command.extend(["--shared-secret-env", launch.shared_secret_env])
    if launch.require_signature:
        command.append("--require-signature")
    for addr in launch.bootstrap_addrs:
        command.extend(["--bootstrap", addr])
    for addr in launch.static_relays:
        command.extend(["--static-relay", addr])
    for addr in launch.advertise_addrs:
        command.extend(["--advertise-addr", addr])
    if launch.rendezvous_addr:
        command.extend(["--rendezvous-addr", launch.rendezvous_addr])
    if launch.rendezvous_namespace:
        command.extend(["--rendezvous-namespace", launch.rendezvous_namespace])
    if launch.rendezvous_ttl_seconds != 7200:
        command.extend(["--rendezvous-ttl-seconds", str(launch.rendezvous_ttl_seconds)])
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


def _watch_command(settings: Settings) -> list[str]:
    return [
        sys.executable or "python",
        "-m",
        "agent_memory_orchestrator.runtime.cli.main",
        "peer-agent",
        "--amo-home",
        str(settings.home),
        "watch",
    ]


def _launchd_label(service_name: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in service_name).strip("-")
    return "com.agent-memory-orchestrator." + (safe or "amo-peer-netd")


def _launchd_domain() -> str:
    return f"gui/{os.getuid()}"


def _launchd_log_dir(settings: Settings) -> Path:
    return settings.home / ".peer" / "netd" / "logs"


def _launchd_plist(settings: Settings, label: str, command: list[str], description: str) -> str:
    log_dir = _launchd_log_dir(settings)
    payload = {
        "Label": label,
        "ProgramArguments": command,
        "WorkingDirectory": str(settings.home),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(log_dir / f"{label}.stdout.log"),
        "StandardErrorPath": str(log_dir / f"{label}.stderr.log"),
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "EnvironmentVariables": {
            "AMO_HOME": str(settings.home),
            "AMO_SERVICE_DESCRIPTION": description,
        },
    }
    return plistlib.dumps(payload, sort_keys=True).decode("utf-8")


def _systemd_unit_name(service_name: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in service_name).strip("-")
    return (safe or "amo-peer-netd") + ".service"


def _systemd_unit(
    unit_name: str,
    settings: Settings,
    command: list[str],
    *,
    description: str = "Agent Memory Orchestrator peer netd",
) -> str:
    return "\n".join(
        [
            "[Unit]",
            f"Description={description}",
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


def _run_commands(commands: list[list[str]], *, ignore_failures: bool = False) -> dict[str, Any]:
    results = []
    ok = True
    for command in commands:
        if not command:
            continue
        result = subprocess.run(command, text=True, capture_output=True, check=False)
        completed = {"command": command} | _completed_process_result(result)
        results.append(completed)
        ok = ok and (ignore_failures or bool(completed["ok"]))
    return {"ok": ok, "results": results}
