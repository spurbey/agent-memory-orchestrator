from __future__ import annotations

import platform
import secrets
import sys
from typing import Any

from .....peer.store import PeerStore

PLACEHOLDER_NODE_IDS = {"", "amo-node", "local-amo", "<device-node-id>", "<device_node_id>"}


def resolve_internal_node_id(store: PeerStore, requested: str = "") -> str:
    requested = _safe_slug(requested)
    if requested and requested not in PLACEHOLDER_NODE_IDS:
        return requested
    config = store.load_config()
    existing = _safe_slug(config.node_id)
    if store.config_path.exists() and existing and existing not in PLACEHOLDER_NODE_IDS:
        return existing
    node_path = store.root / "node_id"
    if node_path.exists():
        saved = _safe_slug(node_path.read_text(encoding="utf-8"))
        if saved and saved not in PLACEHOLDER_NODE_IDS:
            return saved
    generated = _generate_node_id()
    node_path.parent.mkdir(parents=True, exist_ok=True)
    node_path.write_text(generated + "\n", encoding="utf-8")
    return generated


def resolve_display_name(store: PeerStore, requested: str = "", *, yes: bool = False) -> str:
    requested = requested.strip()
    if requested:
        return requested
    config = store.load_config()
    if config.display_name.strip():
        return config.display_name.strip()
    default = _default_display_name()
    if yes or not sys.stdin.isatty():
        return default
    value = input(f"Display name for this device [{default}]: ").strip()
    return value or default


def read_invite_code(requested: str = "", *, yes: bool = False) -> str:
    requested = requested.strip()
    if requested:
        return requested
    if yes or not sys.stdin.isatty():
        raise ValueError("invite code is required; pass --invite-code in non-interactive mode")
    return input("Paste AMO peer invite code: ").strip()


def format_setup_result(result: dict[str, Any]) -> str:
    peer = ((result.get("init") or {}).get("peer") or {}) if isinstance(result.get("init"), dict) else {}
    action = "repair" if result.get("repair") else "setup"
    lines = [
        f"AMO peer {action} complete." if result.get("ok") else f"AMO peer {action} needs attention.",
        f"- Display name: {peer.get('display_name') or ''}",
        f"- Internal node id: {peer.get('node_id') or result.get('node_id') or ''}",
    ]
    if result.get("relay_profile"):
        profile = result["relay_profile"]
        lines.append(f"- Relay profile: {profile.get('name')} ({profile.get('rendezvous_namespace')})")
    if result.get("sidecar"):
        sidecar = result["sidecar"]
        lines.append(f"- Peer sidecar: {'ready' if sidecar.get('ok') else 'check required'}")
    if result.get("netd"):
        netd = result["netd"]
        lines.append(f"- peer-netd: {'running' if netd.get('ok') else 'not running'}")
    if result.get("startup"):
        startup = result["startup"]
        lines.append(f"- Startup/watch: {'installed' if startup.get('ok') else 'failed'}")
    lines.extend(_next_lines(result))
    return "\n".join(lines)


def format_invite_result(result: dict[str, Any]) -> str:
    invite = result.get("invite") if isinstance(result.get("invite"), dict) else {}
    code = str(result.get("invite_code") or "")
    lines = [
        "AMO peer invite created.",
        f"- Invite label: {invite.get('label') or 'AMO peer invite'}",
        f"- Expires: {invite.get('expires_at') or ''}",
        f"- Max uses: {invite.get('max_uses') or 1}",
        "",
        "Send this invite code to your friend:",
        code,
        "",
        "Friend runs:",
        "amo-cli peer join",
        "",
        "Security note: this only establishes a trusted peer transport. Memory, graph data, prompts, and raw evidence are not shared automatically.",
    ]
    return "\n".join(lines)


def format_join_result(result: dict[str, Any]) -> str:
    imported = result.get("accept_invite", {}).get("imported_peer") if isinstance(result.get("accept_invite"), dict) else {}
    lines = [
        "AMO peer join complete." if result.get("ok") else "AMO peer join needs attention.",
    ]
    if imported:
        lines.append(f"- Trusted peer: {imported.get('display_name') or imported.get('node_id')}")
    if result.get("relay_profile"):
        profile = result["relay_profile"]
        lines.append(f"- Relay profile: {profile.get('name')} ({profile.get('rendezvous_namespace')})")
    if result.get("startup"):
        startup = result["startup"]
        lines.append(f"- Startup/watch: {'installed' if startup.get('ok') else 'failed'}")
    lines.extend(_next_lines(result))
    return "\n".join(lines)


def _next_lines(result: dict[str, Any]) -> list[str]:
    commands = [str(item) for item in result.get("next_commands", []) if str(item).strip()]
    if not commands:
        return []
    lines = ["", "Next:"]
    lines.extend(f"{idx}. {command}" for idx, command in enumerate(commands, 1))
    return lines


def _generate_node_id() -> str:
    prefix = _safe_slug(platform.node().lower()) or "amo-device"
    return f"{prefix}-{secrets.token_hex(4)}"


def _default_display_name() -> str:
    name = platform.node().strip()
    return name or "AMO Device"


def _safe_slug(value: str) -> str:
    clean = "".join(ch.lower() for ch in value.strip() if ch.isalnum() or ch in {"-", "_"})
    clean = clean.strip("-_")
    return clean[:64]


__all__ = [
    "PLACEHOLDER_NODE_IDS",
    "format_invite_result",
    "format_join_result",
    "format_setup_result",
    "read_invite_code",
    "resolve_display_name",
    "resolve_internal_node_id",
]
