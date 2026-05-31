from __future__ import annotations

import argparse
import json
from typing import Any

from ....core.config import Settings
from ....peer.invites import decode_invite_code
from ....peer.netd_runtime import PeerNetdLaunchOptions
from ....peer.netd_service import PeerNetdServiceOptions
from ....peer.store import PeerStore


def add_peer_netd_start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--node-id", default="amo-node", help="Stable AMO node id advertised by the sidecar.")
    parser.add_argument("--listen", default="/ip4/0.0.0.0/tcp/0", help="libp2p listen multiaddr.")
    parser.add_argument("--api", default="127.0.0.1:8788", help="Local sidecar API host:port. Must be fixed for managed start.")
    parser.add_argument("--store-path", default="", help="Optional sidecar JSONL inbox path. Defaults under AMO_HOME/.peer/netd.")
    parser.add_argument("--identity-key", default="", help="Optional persistent libp2p identity key path.")
    parser.add_argument(
        "--shared-secret-env",
        default="",
        help="Environment variable containing the shared HMAC secret used by peer-netd.",
    )
    parser.add_argument("--require-signature", action="store_true", help="Reject unsigned incoming peer envelopes.")
    parser.add_argument("--bootstrap", action="append", default=[], help="Bootstrap peer multiaddr. Repeat for multiple peers.")
    parser.add_argument("--static-relay", action="append", default=[], help="Circuit relay multiaddr. Repeat for multiple relays.")
    parser.add_argument(
        "--relay-profile",
        "--relay",
        dest="relay_profile",
        default="",
        help="Saved relay profile name. Expands to --static-relay, --auto-relay, --hole-punching, and rendezvous flags.",
    )
    parser.add_argument("--mdns", action="store_true", help="Enable LAN mDNS discovery.")
    parser.add_argument("--mdns-service", default="_amo-peer._udp", help="mDNS service tag.")
    parser.add_argument("--rendezvous-server", action="store_true", help="Serve AMO rendezvous registration/discovery streams.")
    parser.add_argument("--rendezvous-addr", default="", help="Rendezvous node multiaddr to register with after startup.")
    parser.add_argument("--rendezvous-namespace", default="", help="Rendezvous namespace to register this node under.")
    parser.add_argument("--rendezvous-ttl-seconds", type=int, default=7200, help="Rendezvous registration TTL.")
    parser.add_argument("--relay-service", action="store_true", help="Serve libp2p circuit relay v2 when reachable.")
    parser.add_argument("--nat-service", action="store_true", help="Help peers determine reachability.")
    parser.add_argument("--auto-relay", action="store_true", help="Enable AutoRelay; usually paired with --static-relay.")
    parser.add_argument("--hole-punching", action="store_true", help="Enable libp2p DCUtR hole punching.")
    parser.add_argument("--force-private", action="store_true", help="Force private reachability for relay tests.")
    parser.add_argument("--force-public", action="store_true", help="Force public reachability for relay-service tests.")
    parser.add_argument(
        "--advertise-localhost-dns",
        action="store_true",
        help="Local smoke only: advertise 127.0.0.1 as dns4/localhost.",
    )
    parser.add_argument(
        "--advertise-addr",
        action="append",
        default=[],
        help="Public libp2p listen multiaddr to advertise, e.g. /ip4/1.2.3.4/tcp/4001.",
    )
    parser.add_argument("--no-build", action="store_true", help="Do not build peer-netd automatically if the binary is missing.")


def add_peer_netd_watch_service_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--with-watch",
        action="store_true",
        help="Also install, uninstall, or inspect the peer-agent watch startup entry.",
    )
    parser.add_argument(
        "--watch-service-name",
        default="",
        help="Optional OS startup name for the peer-agent watch entry.",
    )


def peer_netd_options_from_args(args: argparse.Namespace, settings: Settings | None = None) -> PeerNetdLaunchOptions:
    relay_values = relay_values_from_args(args, settings) if settings is not None else {
        "static_relays": tuple(args.static_relay or []),
        "rendezvous_addr": args.rendezvous_addr,
        "rendezvous_namespace": args.rendezvous_namespace,
        "auto_relay": args.auto_relay,
        "hole_punching": args.hole_punching,
    }
    return PeerNetdLaunchOptions(
        node_id=args.node_id,
        listen_addr=args.listen,
        api_addr=args.api,
        store_path=args.store_path,
        identity_key_path=args.identity_key,
        shared_secret_env=args.shared_secret_env,
        require_signature=args.require_signature,
        bootstrap_addrs=tuple(args.bootstrap or []),
        static_relays=tuple(relay_values["static_relays"]),
        mdns=args.mdns,
        mdns_service=args.mdns_service,
        rendezvous_server=args.rendezvous_server,
        relay_service=args.relay_service,
        nat_service=args.nat_service,
        auto_relay=bool(relay_values["auto_relay"]),
        hole_punching=bool(relay_values["hole_punching"]),
        force_private=args.force_private,
        force_public=args.force_public,
        advertise_localhost_dns=args.advertise_localhost_dns,
        advertise_addrs=tuple(args.advertise_addr or []),
        rendezvous_addr=str(relay_values["rendezvous_addr"]),
        rendezvous_namespace=str(relay_values["rendezvous_namespace"]),
        rendezvous_ttl_seconds=args.rendezvous_ttl_seconds,
    )


def relay_values_from_args(args: argparse.Namespace, settings: Settings | None) -> dict[str, Any]:
    static_relays = [str(item).strip() for item in getattr(args, "static_relay", []) or [] if str(item).strip()]
    rendezvous_addr = str(getattr(args, "rendezvous_addr", "") or "").strip()
    rendezvous_namespace = str(getattr(args, "rendezvous_namespace", "") or "").strip()
    auto_relay = bool(getattr(args, "auto_relay", False))
    hole_punching = bool(getattr(args, "hole_punching", False))
    profile_name = str(getattr(args, "relay_profile", "") or "").strip()
    if profile_name:
        if settings is None:
            raise ValueError("--relay requires AMO settings")
        profile = PeerStore(settings).get_relay_profile(profile_name)
        relay_addr = str(profile.get("relay_addr") or "").strip()
        profile_rendezvous_addr = str(profile.get("rendezvous_addr") or relay_addr).strip()
        profile_namespace = str(profile.get("rendezvous_namespace") or "").strip()
        if relay_addr and relay_addr not in static_relays:
            static_relays.insert(0, relay_addr)
        rendezvous_addr = rendezvous_addr or profile_rendezvous_addr
        rendezvous_namespace = rendezvous_namespace or profile_namespace
        auto_relay = auto_relay or bool(profile.get("auto_relay", True))
        hole_punching = hole_punching or bool(profile.get("hole_punching", True))
    return {
        "static_relays": tuple(static_relays),
        "rendezvous_addr": rendezvous_addr,
        "rendezvous_namespace": rendezvous_namespace,
        "auto_relay": auto_relay,
        "hole_punching": hole_punching,
    }


def peer_setup_next_commands(args: argparse.Namespace) -> list[str]:
    commands: list[str] = []
    if not getattr(args, "invite", "") and not getattr(args, "invite_code", ""):
        commands.append(
            "amo-cli peer create-invite --relay "
            + (getattr(args, "relay_profile", "") or "<relay-profile>")
            + " --auto-approve"
        )
    if getattr(args, "install_startup", False):
        commands.extend(
            [
                "amo-cli peer netd service-status --with-watch",
                'amo-cli peer-agent ask --query "<question>"',
            ]
        )
    else:
        commands.extend(
            [
                "amo-cli peer-agent watch",
                'amo-cli peer-agent ask --query "<question>"',
            ]
        )
    return commands


def peer_invite_from_setup_args(args: argparse.Namespace) -> dict[str, Any] | None:
    invite_path = getattr(args, "invite", None)
    invite_code = str(getattr(args, "invite_code", "") or "")
    if invite_code:
        invite = decode_invite_code(invite_code)
    elif invite_path:
        invite = json.loads(invite_path.read_text(encoding="utf-8"))
    else:
        return None
    if not isinstance(invite, dict):
        raise ValueError("peer invite must contain a JSON object")
    return invite


def peer_relay_options_from_args(args: argparse.Namespace) -> PeerNetdLaunchOptions:
    return PeerNetdLaunchOptions(
        node_id=args.node_id,
        listen_addr=args.listen,
        api_addr=args.api,
        store_path=args.store_path,
        rendezvous_server=True,
        relay_service=True,
        nat_service=True,
        force_public=True,
        advertise_addrs=tuple(args.advertise_addr or ()),
    )


def with_relay_next_steps(result: dict[str, Any], namespace: str) -> dict[str, Any]:
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    health = result.get("health") if isinstance(result.get("health"), dict) else status.get("health", {})
    addrs = [str(item) for item in health.get("listen_addrs", []) if str(item).strip()]
    relay_addr = addrs[0] if addrs else ""
    client_enable_args = [
        "peer",
        "enable",
        "--static-relay",
        relay_addr or "<relay-multiaddr>",
        "--auto-relay",
        "--hole-punching",
        "--rendezvous-addr",
        relay_addr or "<relay-multiaddr>",
        "--rendezvous-namespace",
        namespace,
    ]
    invite_flags = [
        "--rendezvous-addr",
        relay_addr or "<relay-multiaddr>",
        "--rendezvous-namespace",
        namespace,
    ]
    return result | {
        "relay": {
            "relay_multiaddr": relay_addr,
            "rendezvous_addr": relay_addr,
            "rendezvous_namespace": namespace,
            "client_enable_args": client_enable_args,
            "create_invite_flags": invite_flags,
            "notes": [
                "Run this helper on an always-on public host or VPS with inbound TCP open for the listen port.",
                "Client devices should start peer netd with --static-relay before creating or accepting invites.",
                "The relay/rendezvous node carries transport streams and discovery records only; AMO room policy and memory stay on user devices.",
            ],
        }
    }


def peer_netd_service_options_from_args(args: argparse.Namespace) -> PeerNetdServiceOptions:
    return PeerNetdServiceOptions(
        service_name=args.service_name,
        apply=getattr(args, "apply", False),
        with_watcher=getattr(args, "with_watch", False),
        watch_service_name=getattr(args, "watch_service_name", ""),
    )


__all__ = [
    "add_peer_netd_start_args",
    "add_peer_netd_watch_service_args",
    "peer_invite_from_setup_args",
    "peer_netd_options_from_args",
    "peer_netd_service_options_from_args",
    "peer_relay_options_from_args",
    "peer_setup_next_commands",
    "relay_values_from_args",
    "with_relay_next_steps",
]
