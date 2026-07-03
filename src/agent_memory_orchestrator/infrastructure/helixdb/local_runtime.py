from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request
from urllib.request import urlopen

DEFAULT_HELIX_CLI_VERSION = "3.0.6"
DEFAULT_HELIX_INSTANCE = "semantic-harness"
DEFAULT_HELIX_PORT = 6969


@dataclass(slots=True, frozen=True)
class LocalHelixRuntimeConfig:
    home: Path
    cli_version: str = DEFAULT_HELIX_CLI_VERSION
    instance: str = DEFAULT_HELIX_INSTANCE
    port: int = DEFAULT_HELIX_PORT

    @property
    def binary_path(self) -> Path:
        suffix = ".exe" if os.name == "nt" else ""
        return self.home / "bin" / f"helix{suffix}"

    @property
    def project_root(self) -> Path:
        return self.home / "helix" / self.instance

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


def ensure_local_helix(config: LocalHelixRuntimeConfig) -> dict[str, Any]:
    docker_version = _docker_server_version()
    installed = _ensure_cli(config)
    initialized = _ensure_project(config)
    started = False
    if not _healthy(config.url):
        _run(
            [str(config.binary_path), "start", config.instance, "--disk", "--persist", "--quiet"],
            cwd=config.project_root,
            timeout=180,
        )
        started = True
        _wait_for_health(config.url, timeout=90)
    payload = {
        "schema_version": "amo-helix-local-setup-v2",
        "status": "ready",
        "url": config.url,
        "instance": config.instance,
        "project_root": str(config.project_root),
        "binary_path": str(config.binary_path),
        "cli_version": _helix_version(config.binary_path),
        "docker_server_version": docker_version,
        "storage": "disk",
        "installed_cli": installed,
        "initialized_project": initialized,
        "started_instance": started,
    }
    manifest = config.project_root / "amo-setup.json"
    manifest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    payload["manifest"] = str(manifest)
    return payload


def local_helix_status(config: LocalHelixRuntimeConfig) -> dict[str, Any]:
    return {
        "status": "ready" if _healthy(config.url) else "unavailable",
        "url": config.url,
        "instance": config.instance,
        "project_root": str(config.project_root),
        "binary_path": str(config.binary_path),
        "cli_installed": config.binary_path.is_file(),
        "project_initialized": (config.project_root / "helix.toml").is_file(),
        "docker_available": shutil.which("docker") is not None,
        "healthy": _healthy(config.url),
    }


def _ensure_cli(config: LocalHelixRuntimeConfig) -> bool:
    if config.binary_path.is_file():
        return False
    config.binary_path.parent.mkdir(parents=True, exist_ok=True)
    asset = _release_asset()
    version = config.cli_version.removeprefix("v")
    url = f"https://github.com/HelixDB/helix-db/releases/download/v{version}/{asset}"
    request = Request(url, headers={"User-Agent": "agent-memory-orchestrator"})
    temporary = config.binary_path.with_suffix(config.binary_path.suffix + ".download")
    try:
        with urlopen(request, timeout=120) as response:  # nosec B310: pinned HTTPS release URL
            temporary.write_bytes(response.read())
        if os.name != "nt":
            temporary.chmod(temporary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        temporary.replace(config.binary_path)
        _helix_version(config.binary_path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def _ensure_project(config: LocalHelixRuntimeConfig) -> bool:
    if (config.project_root / "helix.toml").is_file():
        return False
    config.project_root.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            str(config.binary_path),
            "init",
            "local",
            "--name",
            config.instance,
            "--path",
            str(config.project_root),
            "--port",
            str(config.port),
            "--disk",
            "--no-skills",
            "--quiet",
        ],
        cwd=config.project_root.parent,
        timeout=180,
    )
    return True


def _docker_server_version() -> str:
    if shutil.which("docker") is None:
        raise RuntimeError("docker_not_installed:install_and_start_Docker_Desktop")
    result = _run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=30)
    version = result.stdout.strip()
    if not version:
        raise RuntimeError("docker_daemon_unavailable:start_Docker_Desktop")
    return version


def _helix_version(binary: Path) -> str:
    result = _run([str(binary), "--version"], timeout=30)
    output = (result.stdout or result.stderr).strip()
    if not output:
        raise RuntimeError("helix_cli_version_unavailable")
    return output


def _healthy(url: str) -> bool:
    try:
        with urlopen(f"{url.rstrip('/')}/health", timeout=3) as response:  # nosec B310: loopback URL
            payload = json.loads(response.read().decode("utf-8"))
        return bool(payload.get("healthy"))
    except Exception:
        return False


def _wait_for_health(url: str, *, timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy(url):
            return
        time.sleep(1)
    raise RuntimeError(f"helix_start_timeout:{url}")


def _release_asset() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "aarch64" if machine in {"arm64", "aarch64"} else "x86_64"
    targets = {
        "windows": f"helix-{arch}-pc-windows-msvc.exe",
        "linux": f"helix-{arch}-unknown-linux-gnu",
        "darwin": f"helix-{arch}-apple-darwin",
    }
    if system not in targets:
        raise RuntimeError(f"unsupported_helix_platform:{system}:{machine}")
    return targets[system]


def _run(command: list[str], *, cwd: Path | None = None, timeout: int) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603 - fixed executable and argument lists
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"command_failed:{command[0]}:{result.returncode}:{message}")
    return result


__all__ = [
    "DEFAULT_HELIX_CLI_VERSION",
    "DEFAULT_HELIX_INSTANCE",
    "DEFAULT_HELIX_PORT",
    "LocalHelixRuntimeConfig",
    "ensure_local_helix",
    "local_helix_status",
]
