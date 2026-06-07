from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_binary import binary_capabilities
from agent_memory_orchestrator.peer.netd_binary import build_binary
from agent_memory_orchestrator.peer.netd_binary import go_path
from agent_memory_orchestrator.peer.netd_binary import install_packaged_binary
from agent_memory_orchestrator.peer.netd_binary import packaged_binary_candidates
from agent_memory_orchestrator.peer.netd_binary import packaged_binary_path
from agent_memory_orchestrator.peer.netd_binary import protocol_capabilities
from agent_memory_orchestrator.peer.netd_binary import resolve_binary
from agent_memory_orchestrator.peer.netd_binary import source_dir
from agent_memory_orchestrator.peer.netd_binary import source_build_allowed
from agent_memory_orchestrator.peer.netd_binary import source_dir_candidates
from agent_memory_orchestrator.peer.netd_client import PeerNetdClient, PeerNetdError
from agent_memory_orchestrator.peer.netd_errors import PeerNetdRuntimeError
from agent_memory_orchestrator.peer.netd_platform import _creation_flags
from agent_memory_orchestrator.peer.netd_platform import _missing_binary_requirements
from agent_memory_orchestrator.peer.netd_platform import _tail_text
from agent_memory_orchestrator.peer.netd_platform import binary_name
from agent_memory_orchestrator.peer.netd_platform import platform_binary_dir_name


REQUIRED_NETD_FLAGS = ("identity-key", "advertise-addr")
REQUIRED_NETD_PROTOCOL_CAPABILITIES = ("remote_peer_id",)

__all__ = [
    "PeerNetdLaunchOptions",
    "PeerNetdRuntime",
    "PeerNetdRuntimeError",
    "REQUIRED_NETD_FLAGS",
    "REQUIRED_NETD_PROTOCOL_CAPABILITIES",
    "binary_name",
    "platform_binary_dir_name",
]


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
        target = output_path or self.default_binary_path()
        return build_binary(source_dir=self.source_dir(), target=target, go_path=self.go_path())

    def start(self, options: PeerNetdLaunchOptions, build_if_missing: bool = True) -> dict[str, Any]:
        if options.api_addr.endswith(":0"):
            raise PeerNetdRuntimeError("managed peer-netd start requires a fixed --api host:port, not port 0")

        current = self.status()
        desired_launch = self.launch_config(options)
        restart_result: dict[str, Any] | None = None
        if current.get("running") and current.get("api_ok"):
            current_state = self.read_state()
            current_launch = current_state.get("launch_config") if isinstance(current_state.get("launch_config"), dict) else None
            if current_launch == desired_launch:
                current_binary = Path(str(current_state.get("binary") or self.resolve_binary()))
                current_capabilities = self.binary_capabilities(current_binary)
                if _missing_binary_requirements(current_capabilities):
                    restart_result = self.stop()
                else:
                    api_url = str(current.get("api_url") or self.api_url(options.api_addr))
                    post_start = self.post_start(options, api_url)
                    if post_start:
                        current["health"] = PeerNetdClient(base_url=api_url, timeout_seconds=1.0).health()
                    return {
                        "ok": True,
                        "already_running": True,
                        "launch_config_match": True,
                        "status": current,
                        "post_start": post_start,
                    }
            else:
                restart_result = self.stop()

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
            "launch_config": desired_launch,
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
            "restart": restart_result,
            "pid": process.pid,
            "api_url": state["api_url"],
            "health": health,
            "post_start": post_start,
            "logs": {"stdout": str(stdout_path), "stderr": str(stderr_path)},
        }

    def prepare_binary(self, *, build_if_missing: bool = True) -> Path:
        allow_source_build = bool(build_if_missing and source_build_allowed())
        binary = self.resolve_binary()
        packaged = self.packaged_binary_path()
        if packaged is not None and binary.resolve() == packaged.resolve():
            binary = self.install_packaged_binary(packaged)
        elif not binary.exists():
            if packaged is not None:
                binary = self.install_packaged_binary(packaged, binary)
            elif not allow_source_build:
                raise PeerNetdRuntimeError(
                    "peer_sidecar_unavailable: signed amo-peer-netd sidecar is not installed"
                    f" ({binary}); run install with --with-peer or provide AMO_PEER_NETD_BIN"
                )
            else:
                self.build(binary)
        elif not binary.is_file():
            raise PeerNetdRuntimeError(f"peer-netd binary path is not a file: {binary}")

        capabilities = self.binary_capabilities(binary)
        if not _missing_binary_requirements(capabilities):
            return binary

        packaged_is_different = packaged is not None and packaged.exists() and packaged.resolve() != binary.resolve()
        if packaged_is_different:
            packaged_capabilities = self.binary_capabilities(packaged)
            if not _missing_binary_requirements(packaged_capabilities):
                return self.install_packaged_binary(packaged, binary)

        missing = ", ".join(_missing_binary_requirements(capabilities))
        if not allow_source_build:
            raise PeerNetdRuntimeError(
                "peer_sidecar_unavailable: peer-netd binary is stale or incompatible"
                f" (missing: {missing}); run install with --with-peer or provide a current signed sidecar"
            )
        self.build(binary)
        rebuilt = self.binary_capabilities(binary)
        if _missing_binary_requirements(rebuilt):
            missing_after = ", ".join(_missing_binary_requirements(rebuilt))
            raise PeerNetdRuntimeError(f"rebuilt peer-netd is still missing required flags: {missing_after}")
        return binary

    def binary_capabilities(self, binary: Path | None = None) -> dict[str, Any]:
        candidate = (binary or self.resolve_binary()).expanduser().resolve()
        return binary_capabilities(
            candidate,
            required_flags=REQUIRED_NETD_FLAGS,
            required_protocol_capabilities=REQUIRED_NETD_PROTOCOL_CAPABILITIES,
        )

    def protocol_capabilities(self, binary: Path) -> dict[str, Any]:
        return protocol_capabilities(binary)

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

    def launch_config(self, options: PeerNetdLaunchOptions) -> dict[str, Any]:
        """Return a stable config fingerprint for deciding sidecar reuse.

        Values that affect peer identity, network reachability, or inbox location
        must match before an already-running daemon can be reused.
        """
        return {
            "node_id": options.node_id,
            "listen_addr": options.listen_addr,
            "api_addr": options.api_addr,
            "store_path": str(self.store_path(options)),
            "identity_key_path": str(self.identity_key_path(options)),
            "shared_secret_env": options.shared_secret_env,
            "require_signature": options.require_signature,
            "bootstrap_addrs": list(options.bootstrap_addrs),
            "static_relays": list(options.static_relays),
            "mdns": options.mdns,
            "mdns_service": options.mdns_service,
            "auto_connect_discovered": options.auto_connect_discovered,
            "rendezvous_server": options.rendezvous_server,
            "relay_service": options.relay_service,
            "nat_service": options.nat_service,
            "auto_relay": options.auto_relay,
            "hole_punching": options.hole_punching,
            "force_private": options.force_private,
            "force_public": options.force_public,
            "advertise_localhost_dns": options.advertise_localhost_dns,
            "advertise_addrs": list(options.advertise_addrs),
            "rendezvous_addr": options.rendezvous_addr,
            "rendezvous_namespace": options.rendezvous_namespace,
            "rendezvous_ttl_seconds": options.rendezvous_ttl_seconds,
        }

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
        return resolve_binary(
            self.binary_path,
            default_path=self.default_binary_path(),
            packaged_path=self.packaged_binary_path(),
        )

    def install_packaged_binary(self, source: Path, target: Path | None = None) -> Path:
        target = target or self.default_binary_path()
        return install_packaged_binary(source, target)

    def packaged_binary_path(self) -> Path | None:
        return packaged_binary_path(self.packaged_binary_candidates())

    def packaged_binary_candidates(self) -> list[Path]:
        return packaged_binary_candidates(repo_root=self.repo_root)

    def source_dir(self) -> Path:
        return source_dir(self.source_dir_candidates())

    def source_dir_candidates(self) -> list[Path]:
        return source_dir_candidates(repo_root=self.repo_root)

    def go_path(self) -> str:
        return go_path(self.source_dir())

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
