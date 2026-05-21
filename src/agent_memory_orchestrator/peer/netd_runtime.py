from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_client import PeerNetdClient, PeerNetdError


class PeerNetdRuntimeError(RuntimeError):
    """Raised when the managed peer sidecar cannot be built or controlled."""


REQUIRED_NETD_FLAGS = ("identity-key", "advertise-addr")


@dataclass(slots=True, frozen=True)
class PeerNetdLaunchOptions:
    node_id: str = "amo-node"
    listen_addr: str = "/ip4/0.0.0.0/tcp/0"
    api_addr: str = "127.0.0.1:8788"
    store_path: str = ""
    identity_key_path: str = ""
    shared_secret_env: str = ""
    require_signature: bool = False
    bootstrap_addrs: tuple[str, ...] = ()
    static_relays: tuple[str, ...] = ()
    mdns: bool = False
    mdns_service: str = "_amo-peer._udp"
    auto_connect_discovered: bool = True
    rendezvous_server: bool = False
    relay_service: bool = False
    nat_service: bool = False
    auto_relay: bool = False
    hole_punching: bool = False
    force_private: bool = False
    force_public: bool = False
    advertise_localhost_dns: bool = False
    advertise_addrs: tuple[str, ...] = ()
    rendezvous_addr: str = ""
    rendezvous_namespace: str = ""
    rendezvous_ttl_seconds: int = 7200


@dataclass(slots=True)
class PeerNetdRuntime:
    """Build and supervise the Go libp2p sidecar from AMO.

    AMO's Python process remains responsible for memory policy and room state.
    This runtime only manages the local network daemon lifecycle.
    """

    settings: Settings
    binary_path: Path | None = None
    repo_root: Path | None = None
    health_timeout_seconds: float = 8.0
    state_filename: str = "netd.json"

    def build(self, output_path: Path | None = None) -> dict[str, Any]:
        source_dir = self.source_dir()
        if not source_dir.exists():
            raise PeerNetdRuntimeError(f"peer-netd source directory not found: {source_dir}")

        go_path = self.go_path()
        if not go_path:
            raise PeerNetdRuntimeError("Go toolchain not found; install Go or set PATH before building peer-netd")

        target = output_path or self.default_binary_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [go_path, "build", "-o", str(target), ".\\cmd\\amo-peer-netd" if os.name == "nt" else "./cmd/amo-peer-netd"],
            cwd=source_dir,
            text=True,
            capture_output=True,
            check=False,
            timeout=180,
        )
        if result.returncode != 0:
            raise PeerNetdRuntimeError(f"go build failed: {result.stderr.strip() or result.stdout.strip()}")
        return {
            "ok": True,
            "binary": str(target),
            "source_dir": str(source_dir),
            "go": go_path,
        }

    def start(self, options: PeerNetdLaunchOptions, build_if_missing: bool = True) -> dict[str, Any]:
        if options.api_addr.endswith(":0"):
            raise PeerNetdRuntimeError("managed peer-netd start requires a fixed --api host:port, not port 0")

        current = self.status()
        if current.get("running") and current.get("api_ok"):
            api_url = str(current.get("api_url") or self.api_url(options.api_addr))
            post_start = self.post_start(options, api_url)
            if post_start:
                current["health"] = PeerNetdClient(base_url=api_url, timeout_seconds=1.0).health()
            return {"ok": True, "already_running": True, "status": current, "post_start": post_start}

        binary = self.prepare_binary(build_if_missing=build_if_missing)

        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stdout_path = self.log_dir / f"netd-{timestamp}.stdout.log"
        stderr_path = self.log_dir / f"netd-{timestamp}.stderr.log"
        args = self.args_for(binary, options)

        stdout_file = stdout_path.open("ab")
        stderr_file = stderr_path.open("ab")
        try:
            process = subprocess.Popen(
                args,
                cwd=self.source_dir() if self.source_dir().exists() else None,
                stdout=stdout_file,
                stderr=stderr_file,
                stdin=subprocess.DEVNULL,
                creationflags=_creation_flags(),
            )
        finally:
            stdout_file.close()
            stderr_file.close()

        state = {
            "pid": process.pid,
            "api_addr": options.api_addr,
            "api_url": self.api_url(options.api_addr),
            "binary": str(binary),
            "store_path": str(self.store_path(options)),
            "started_at": timestamp,
            "args": args,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
        self.write_state(state)

        try:
            health = self.wait_for_health(options.api_addr)
            post_start = self.post_start(options, state["api_url"])
            if post_start:
                health = PeerNetdClient(base_url=state["api_url"], timeout_seconds=1.0).health()
        except Exception as exc:
            tail = _tail_text(stderr_path)
            self.stop()
            raise PeerNetdRuntimeError(f"peer-netd failed to become healthy: {exc}; stderr_tail={tail!r}") from exc

        return {
            "ok": True,
            "already_running": False,
            "pid": process.pid,
            "api_url": state["api_url"],
            "health": health,
            "post_start": post_start,
            "logs": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
        }

    def prepare_binary(self, *, build_if_missing: bool = True) -> Path:
        binary = self.resolve_binary()
        packaged = self.packaged_binary_path()
        if packaged is not None and binary.resolve() == packaged.resolve():
            binary = self.install_packaged_binary(packaged)
        elif not binary.exists():
            if packaged is not None:
                binary = self.install_packaged_binary(packaged, binary)
            elif not build_if_missing:
                raise PeerNetdRuntimeError(f"peer-netd binary not found: {binary}")
            else:
                self.build(binary)
        elif not binary.is_file():
            raise PeerNetdRuntimeError(f"peer-netd binary path is not a file: {binary}")

        capabilities = self.binary_capabilities(binary)
        if not capabilities.get("missing_required_flags"):
            return binary

        packaged_is_different = packaged is not None and packaged.exists() and packaged.resolve() != binary.resolve()
        if packaged_is_different:
            packaged_capabilities = self.binary_capabilities(packaged)
            if not packaged_capabilities.get("missing_required_flags"):
                return self.install_packaged_binary(packaged, binary)

        missing = ", ".join(str(item) for item in capabilities.get("missing_required_flags", []))
        if not build_if_missing:
            raise PeerNetdRuntimeError(
                "peer-netd binary is stale or incompatible"
                f" (missing flags: {missing}); run amo-cli peer netd build or reinstall/update AMO"
            )
        self.build(binary)
        rebuilt = self.binary_capabilities(binary)
        if rebuilt.get("missing_required_flags"):
            missing_after = ", ".join(str(item) for item in rebuilt.get("missing_required_flags", []))
            raise PeerNetdRuntimeError(f"rebuilt peer-netd is still missing required flags: {missing_after}")
        return binary

    def binary_capabilities(self, binary: Path | None = None) -> dict[str, Any]:
        candidate = (binary or self.resolve_binary()).expanduser().resolve()
        if not candidate.exists():
            return {
                "ok": False,
                "binary": str(candidate),
                "exists": False,
                "missing_required_flags": list(REQUIRED_NETD_FLAGS),
            }
        try:
            result = subprocess.run(
                [str(candidate), "-h"],
                text=True,
                capture_output=True,
                check=False,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return {
                "ok": False,
                "binary": str(candidate),
                "exists": True,
                "executable": False,
                "error": str(exc),
                "missing_required_flags": list(REQUIRED_NETD_FLAGS),
            }
        help_text = f"{result.stdout}\n{result.stderr}"
        missing = [
            flag
            for flag in REQUIRED_NETD_FLAGS
            if f"-{flag}" not in help_text and f"--{flag}" not in help_text
        ]
        return {
            "ok": not missing,
            "binary": str(candidate),
            "exists": True,
            "executable": True,
            "returncode": result.returncode,
            "missing_required_flags": missing,
        }

    def stop(self) -> dict[str, Any]:
        state = self.read_state()
        pid = int(state.get("pid") or 0)
        if not pid:
            return {"ok": True, "stopped": False, "reason": "no managed peer-netd state"}

        if not self.is_process_alive(pid):
            self.clear_state()
            return {"ok": True, "stopped": False, "reason": "managed peer-netd was not running", "pid": pid}

        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if not self.is_process_alive(pid):
                self.clear_state()
                return {"ok": True, "stopped": True, "pid": pid}
            time.sleep(0.1)

        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], text=True, capture_output=True, check=False)
        else:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        self.clear_state()
        return {"ok": True, "stopped": True, "forced": True, "pid": pid}

    def status(self) -> dict[str, Any]:
        state = self.read_state()
        pid = int(state.get("pid") or 0)
        running = bool(pid and self.is_process_alive(pid))
        api_url = str(state.get("api_url") or "")
        api_ok = False
        health: dict[str, Any] | None = None
        api_error = ""
        if api_url:
            try:
                health = PeerNetdClient(base_url=api_url, timeout_seconds=1.5).health()
                api_ok = True
            except PeerNetdError as exc:
                api_error = str(exc)
        return {
            "ok": True,
            "running": running,
            "api_ok": api_ok,
            "pid": pid or None,
            "api_url": api_url or None,
            "health": health,
            "api_error": api_error,
            "store_path": state.get("store_path") or str(self.store_path(PeerNetdLaunchOptions())),
            "state_path": str(self.state_path),
            "binary": state.get("binary"),
            "logs": {
                "stdout": state.get("stdout_log"),
                "stderr": state.get("stderr_log"),
            },
        }

    def wait_for_health(self, api_addr: str) -> dict[str, Any]:
        client = PeerNetdClient(base_url=self.api_url(api_addr), timeout_seconds=1.0)
        deadline = time.monotonic() + self.health_timeout_seconds
        last_error = ""
        while time.monotonic() < deadline:
            try:
                return client.health()
            except PeerNetdError as exc:
                last_error = str(exc)
                time.sleep(0.15)
        raise PeerNetdRuntimeError(last_error or "health check timed out")

    def post_start(self, options: PeerNetdLaunchOptions, api_url: str) -> dict[str, Any]:
        if not (options.rendezvous_addr and options.rendezvous_namespace):
            return {}
        client = PeerNetdClient(base_url=api_url, timeout_seconds=3.0)
        return {
            "rendezvous_registration": client.rendezvous_register(
                options.rendezvous_addr,
                options.rendezvous_namespace,
                ttl_seconds=options.rendezvous_ttl_seconds,
            )
        }

    def args_for(self, binary: Path, options: PeerNetdLaunchOptions) -> list[str]:
        args = [
            str(binary),
            "--node-id",
            options.node_id,
            "--listen",
            options.listen_addr,
            "--api",
            options.api_addr,
            "--store-path",
            str(self.store_path(options)),
            "--identity-key",
            str(self.identity_key_path(options)),
            "--mdns-service",
            options.mdns_service,
        ]
        if options.shared_secret_env:
            secret = os.getenv(options.shared_secret_env, "")
            if not secret:
                raise PeerNetdRuntimeError(f"shared secret env var is not set: {options.shared_secret_env}")
            args.extend(["--shared-secret", secret])
        if options.require_signature:
            args.append("--require-signature")
        if options.mdns:
            args.append("--mdns")
        if not options.auto_connect_discovered:
            # The sidecar defaults to true; no negative flag exists yet.
            raise PeerNetdRuntimeError("auto_connect_discovered=false is not supported by peer-netd flags yet")
        if options.rendezvous_server:
            args.append("--rendezvous-server")
        if options.relay_service:
            args.append("--relay-service")
        if options.nat_service:
            args.append("--nat-service")
        if options.auto_relay:
            args.append("--auto-relay")
        if options.hole_punching:
            args.append("--hole-punching")
        if options.force_private:
            args.append("--force-private")
        if options.force_public:
            args.append("--force-public")
        if options.advertise_localhost_dns:
            args.append("--advertise-localhost-dns")
        for addr in options.advertise_addrs:
            args.extend(["--advertise-addr", addr])
        for addr in options.bootstrap_addrs:
            args.extend(["--bootstrap", addr])
        for addr in options.static_relays:
            args.extend(["--static-relay", addr])
        return args

    @property
    def runtime_dir(self) -> Path:
        return self.settings.home / ".peer" / "netd"

    @property
    def bin_dir(self) -> Path:
        return self.settings.home / ".peer" / "bin"

    @property
    def log_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def state_path(self) -> Path:
        return self.runtime_dir / self.state_filename

    def store_path(self, options: PeerNetdLaunchOptions) -> Path:
        if options.store_path:
            return Path(options.store_path).expanduser().resolve()
        return self.runtime_dir / "inbox.jsonl"

    def identity_key_path(self, options: PeerNetdLaunchOptions) -> Path:
        if options.identity_key_path:
            return Path(options.identity_key_path).expanduser().resolve()
        return self.runtime_dir / "identity.key"

    def default_binary_path(self) -> Path:
        return self.bin_dir / binary_name()

    def resolve_binary(self) -> Path:
        if self.binary_path:
            return self.binary_path.expanduser().resolve()
        env_path = os.getenv("AMO_PEER_NETD_BIN", "").strip()
        if env_path:
            return Path(env_path).expanduser().resolve()
        default_path = self.default_binary_path()
        if default_path.exists():
            return default_path
        found = shutil.which(binary_name())
        if found:
            return Path(found).resolve()
        packaged = self.packaged_binary_path()
        if packaged is not None:
            return packaged
        return default_path

    def install_packaged_binary(self, source: Path, target: Path | None = None) -> Path:
        target = target or self.default_binary_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        if os.name != "nt":
            target.chmod(target.stat().st_mode | 0o111)
        return target

    def packaged_binary_path(self) -> Path | None:
        for candidate in self.packaged_binary_candidates():
            if candidate.exists() and candidate.is_file():
                return candidate
        return None

    def packaged_binary_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        installed_package_root = Path(__file__).resolve().parents[1]
        candidates.append(installed_package_root / "bin" / platform_binary_dir_name() / binary_name())
        for source_dir in self.source_dir_candidates():
            candidates.append(source_dir / "bin" / platform_binary_dir_name() / binary_name())

        prefix_root = Path(sys.prefix).resolve()
        candidates.extend(
            [
                prefix_root / "bin" / platform_binary_dir_name() / binary_name(),
                prefix_root / "Scripts" / platform_binary_dir_name() / binary_name(),
            ]
        )
        return list(dict.fromkeys(candidates))

    def source_dir(self) -> Path:
        for candidate in self.source_dir_candidates():
            if candidate.exists():
                return candidate
        return self.source_dir_candidates()[0]

    def source_dir_candidates(self) -> list[Path]:
        if self.repo_root:
            return [self.repo_root / "peer-netd"]

        package_root = Path(__file__).resolve().parents[3]
        prefix_root = Path(sys.prefix).resolve()
        candidates = [
            package_root / "peer-netd",
            prefix_root / "peer-netd",
            prefix_root / "Lib" / "peer-netd",
            prefix_root / "share" / "peer-netd",
        ]
        return list(dict.fromkeys(candidates))

    def go_path(self) -> str:
        found = shutil.which("go")
        if found:
            return found
        bundled = self.source_dir().parent / ".tmp" / "tools" / "go" / "bin" / ("go.exe" if os.name == "nt" else "go")
        return str(bundled) if bundled.exists() else ""

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def clear_state(self) -> None:
        if self.state_path.exists():
            self.state_path.unlink()

    def is_process_alive(self, pid: int) -> bool:
        if pid <= 0:
            return False
        if os.name == "nt":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                capture_output=True,
                check=False,
            )
            return str(pid) in result.stdout
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    @staticmethod
    def api_url(api_addr: str) -> str:
        return "http://" + api_addr.removeprefix("http://").removeprefix("https://")


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
