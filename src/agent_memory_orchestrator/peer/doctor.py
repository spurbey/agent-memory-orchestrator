from __future__ import annotations

import os
from typing import Any

from ..core.config import Settings
from .netd_runtime import PeerNetdRuntime
from .store import PeerStore


def peer_doctor(settings: Settings) -> dict[str, Any]:
    """Return an operator-facing readiness report for peer rooms."""

    store = PeerStore(settings)
    runtime = PeerNetdRuntime(settings)
    config_exists = store.config_path.exists()
    config = store.load_config()
    status = runtime.status()
    source_dir = runtime.source_dir()
    binary_path = runtime.resolve_binary()
    go_path = runtime.go_path()

    checks: list[dict[str, Any]] = []

    def add_check(name: str, status_value: str, detail: str, action: str = "") -> None:
        checks.append(
            {
                "name": name,
                "status": status_value,
                "detail": detail,
                "action": action,
            }
        )

    if config_exists:
        add_check("peer_identity", "pass", f"configured as {config.node_id}")
    else:
        add_check(
            "peer_identity",
            "fail",
            "peer identity has not been initialized",
            'amo-cli peer init --node-id <device-name> --display-name "<Device Name>"',
        )

    source_ok = (source_dir / "go.mod").exists() and (source_dir / "cmd" / "amo-peer-netd" / "main.go").exists()
    if source_ok:
        add_check("netd_source", "pass", f"found peer-netd source at {source_dir}")
    else:
        add_check(
            "netd_source",
            "fail",
            f"peer-netd source not found at {source_dir}",
            "reinstall/update agent-memory-orchestrator-cli so packaged peer-netd sources are available",
        )

    if binary_path.exists():
        add_check("netd_binary", "pass", f"found sidecar binary at {binary_path}")
    elif go_path:
        add_check(
            "netd_binary",
            "warn",
            "sidecar binary is not built yet, but Go is available",
            "amo-cli peer netd build",
        )
    else:
        add_check(
            "netd_binary",
            "fail",
            "sidecar binary is missing and Go is not on PATH",
            "install Go 1.22+ or install a package that includes a prebuilt amo-peer-netd binary",
        )

    if status.get("running") and status.get("api_ok"):
        add_check("netd_runtime", "pass", f"running at {status.get('api_url')}")
    elif status.get("running"):
        add_check(
            "netd_runtime",
            "warn",
            f"process is running but local API is not healthy: {status.get('api_error') or 'unknown error'}",
            "amo-cli peer netd status; inspect stderr log from the status output",
        )
    else:
        add_check(
            "netd_runtime",
            "warn",
            "sidecar is not running",
            f"amo-cli peer enable --node-id {config.node_id}",
        )

    if config.peers:
        add_check("trusted_peers", "pass", f"{len(config.peers)} configured peer(s)")
    else:
        add_check(
            "trusted_peers",
            "warn",
            "no trusted peers imported yet",
            "amo-cli peer import-card --file <peer.card.json>",
        )

    missing_secret_envs = sorted(
        {
            peer.shared_secret_env
            for peer in config.peers
            if peer.shared_secret_env and not os.getenv(peer.shared_secret_env)
        }
    )
    if missing_secret_envs:
        add_check(
            "shared_secrets",
            "fail",
            "configured shared-secret environment variables are not set",
            "set: " + ", ".join(missing_secret_envs),
        )
    else:
        add_check("shared_secrets", "pass", "configured shared-secret environment variables are available")

    blocking = [check for check in checks if check["status"] == "fail"]
    ready_for_rooms = not blocking and bool(status.get("running") and status.get("api_ok"))

    return {
        "ok": True,
        "ready": ready_for_rooms,
        "blocking_count": len(blocking),
        "warning_count": sum(1 for check in checks if check["status"] == "warn"),
        "node_id": config.node_id,
        "config_path": str(store.config_path),
        "source_dir": str(source_dir),
        "binary": str(binary_path),
        "go": go_path,
        "netd": status,
        "checks": checks,
        "next_commands": _next_commands(config.node_id, config_exists=config_exists, ready=ready_for_rooms),
    }


def _next_commands(node_id: str, *, config_exists: bool, ready: bool) -> list[str]:
    if ready:
        return [
            "amo-cli peer share-card --out <device>.card.json",
            'amo-cli peer open-room --topic "<topic>" --peer <peer-node-id>',
        ]
    if not config_exists:
        return [
            'amo-cli peer init --node-id <device-name> --display-name "<Device Name>"',
            "amo-cli peer enable --node-id <device-name>",
            "amo-cli peer share-card --out <device>.card.json",
        ]
    return [
        f"amo-cli peer enable --node-id {node_id}",
        "amo-cli peer share-card --out <device>.card.json",
        "amo-cli peer import-card --file <peer.card.json>",
    ]
