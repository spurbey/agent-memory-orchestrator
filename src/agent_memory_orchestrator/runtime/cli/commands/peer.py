from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....peer import PeerService
from ....peer.agent import PeerAgentService
from ....peer.doctor import peer_doctor
from ....peer.invites import decode_invite_code
from ....peer.netd_runtime import PeerNetdLaunchOptions
from ....peer.netd_runtime import PeerNetdRuntime
from ....peer.netd_service import PeerNetdServiceOptions
from ....peer.netd_service import install_service as install_peer_netd_service
from ....peer.netd_service import service_status as peer_netd_service_status
from ....peer.netd_service import uninstall_service as uninstall_peer_netd_service
from ....peer.server import main as peer_server_main
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


def add_peer_subcommands(sub: Any) -> None:
    peer = sub.add_parser("peer", help="Configure AMO peer rooms and the local libp2p sidecar")
    peer.add_argument("--amo-home", type=Path, help="AMO home directory containing peer config and room state.")
    peer_sub = peer.add_subparsers(dest="peer_command", required=True)
    peer_doctor_cmd = peer_sub.add_parser("doctor", help="Check peer identity, netd source, binary, sidecar, and peers")
    peer_doctor_cmd.add_argument("--strict", action="store_true", help="Return non-zero unless peer rooms are ready now.")
    peer_init = peer_sub.add_parser("init", help="Initialize this AMO node's peer identity")
    peer_init.add_argument("--node-id", required=True)
    peer_init.add_argument("--display-name", default="")
    peer_init.add_argument("--capability", action="append", default=[])
    peer_add = peer_sub.add_parser("add", help="Add a trusted peer identity and optional transport addresses")
    peer_add.add_argument("--node-id", required=True)
    peer_add.add_argument("--base-url", default="", help="Legacy direct HTTP URL, e.g. http://100.76.18.75:8787")
    peer_add.add_argument("--peer-id", default="", help="libp2p peer id for amo-peer-netd delivery.")
    peer_add.add_argument("--multiaddr", action="append", default=[], help="Dialable libp2p multiaddr. Repeat as needed.")
    peer_add.add_argument("--relay-addr", action="append", default=[], help="Dialable relay /p2p-circuit multiaddr. Repeat as needed.")
    peer_add.add_argument("--rendezvous-addr", default="", help="Rendezvous node multiaddr used for discovery.")
    peer_add.add_argument("--rendezvous-namespace", default="", help="Rendezvous namespace for this peer/group.")
    peer_add.add_argument("--display-name", default="")
    peer_add.add_argument("--capability", action="append", default=[])
    peer_add.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="trusted")
    peer_add.add_argument(
        "--shared-secret-env",
        default="",
        help="Optional environment variable containing this peer's HMAC shared secret.",
    )
    peer_remove = peer_sub.add_parser("remove", help="Remove a configured peer identity")
    peer_remove.add_argument("--node-id", required=True)
    peer_sub.add_parser("status", help="Show peer node, policy, configured peers, and room count")
    peer_share = peer_sub.add_parser("share-card", help="Print or write this node's importable peer card")
    peer_share.add_argument("--out", type=Path, help="Optional JSON output path.")
    peer_share.add_argument("--base-url", default="", help="Optional legacy direct HTTP URL to include.")
    peer_share.add_argument("--rendezvous-addr", default="", help="Optional rendezvous node multiaddr to include.")
    peer_share.add_argument("--rendezvous-namespace", default="", help="Optional rendezvous namespace to include.")
    peer_share.add_argument("--relay-profile", "--relay", dest="relay_profile", default="", help="Saved relay profile to include in this card.")
    peer_import = peer_sub.add_parser("import-card", help="Import a trusted peer from a peer-card JSON file")
    peer_import.add_argument("--file", required=True, type=Path)
    peer_import.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="trusted")
    peer_import.add_argument("--shared-secret-env", default="")
    peer_invite = peer_sub.add_parser("create-invite", help="Create a shareable peer invite bundle/code")
    peer_invite.add_argument("--out", type=Path, help="Optional JSON invite output path.")
    peer_invite.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="trusted")
    peer_invite.add_argument("--shared-secret-env", default="")
    peer_invite.add_argument("--label", default="", help="Optional human-readable invite label.")
    peer_invite.add_argument("--base-url", default="", help="Optional legacy direct HTTP URL to include.")
    peer_invite.add_argument("--rendezvous-addr", default="", help="Optional rendezvous node multiaddr to include.")
    peer_invite.add_argument("--rendezvous-namespace", default="", help="Optional rendezvous namespace to include.")
    peer_invite.add_argument("--relay-profile", "--relay", dest="relay_profile", default="", help="Saved relay profile to include in this invite.")
    peer_invite.add_argument("--auto-approve", action="store_true", help="Auto-import the accepting peer after token proof.")
    peer_invite.add_argument("--expires-minutes", type=int, default=1440, help="Invite validity window.")
    peer_invite.add_argument("--max-uses", type=int, default=1, help="Maximum accepted join requests.")
    peer_accept = peer_sub.add_parser("accept-invite", help="Import an invite and optionally write this node's response card")
    peer_accept_source = peer_accept.add_mutually_exclusive_group(required=True)
    peer_accept_source.add_argument("--file", type=Path, help="Invite JSON file to accept.")
    peer_accept_source.add_argument("--code", default="", help="amo-peer-invite: code to accept.")
    peer_accept.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="")
    peer_accept.add_argument("--shared-secret-env", default="")
    peer_accept.add_argument("--response-out", type=Path, help="Optional response peer-card JSON path to send back.")
    peer_accept.add_argument("--no-send-join-request", action="store_true", help="Do not send the automatic return join request.")
    peer_join_requests = peer_sub.add_parser("join-requests", help="List pending peer join requests")
    peer_join_requests.add_argument("--status", default="", choices=["", "pending", "approved", "rejected"])
    peer_approve_join = peer_sub.add_parser("approve-join", help="Approve a pending peer join request")
    peer_approve_join.add_argument("--request-id", required=True)
    peer_reject_join = peer_sub.add_parser("reject-join", help="Reject a pending peer join request")
    peer_reject_join.add_argument("--request-id", required=True)
    peer_reject_join.add_argument("--reason", default="")
    peer_sub.add_parser("rooms", help="List local peer investigation rooms")
    peer_context = peer_sub.add_parser("context", help="Build the three-layer context pack for a room")
    peer_context.add_argument("--room-id", required=True)
    peer_context.add_argument("--viewer-node-id", default="", help="Defaults to this AMO node id")
    peer_message = peer_sub.add_parser("append-message", help="Append a local peer-room message for smoke tests/manual use")
    peer_message.add_argument("--room-id", required=True)
    peer_message.add_argument("--from-node-id", required=True)
    peer_message.add_argument("--to-node-id", action="append", default=[])
    peer_message.add_argument("--type", default="context_request")
    peer_message.add_argument("--content", required=True)
    peer_message.add_argument("--citation", action="append", default=[])
    peer_message.add_argument("--confidence", type=float)
    peer_send = peer_sub.add_parser("send-message", help="Append and send a room message through amo-peer-netd")
    peer_send.add_argument("--room-id", required=True)
    peer_send.add_argument("--peer-id", required=True, help="Configured AMO peer node id, not the libp2p peer id.")
    peer_send.add_argument("--type", default="context_request")
    peer_send.add_argument("--content", required=True)
    peer_send.add_argument("--citation", action="append", default=[])
    peer_send.add_argument("--confidence", type=float)
    peer_summary = peer_sub.add_parser("update-summary", help="Replace a room's initiator-owned rolling summary")
    peer_summary.add_argument("--room-id", required=True)
    peer_summary.add_argument("--summary", required=True)
    peer_room = peer_sub.add_parser("open-room", help="Create an investigation room and invite configured peers")
    peer_room.add_argument("--topic", required=True)
    peer_room.add_argument("--peer", action="append", default=[], help="Peer node id to invite. Repeat for multiple peers.")
    peer_room.add_argument("--no-send", action="store_true", help="Create the room locally without sending invites.")
    peer_setup = peer_sub.add_parser("setup", help="One-time peer setup: init identity, start relay sidecar, and optionally install startup")
    add_peer_netd_start_args(peer_setup)
    peer_setup.add_argument("--display-name", default="", help="Display name to save for this AMO peer identity.")
    peer_setup.add_argument("--capability", action="append", default=[], help="Capability to save on first setup. Repeat as needed.")
    peer_setup.add_argument("--relay-addr", default="", help="Relay multiaddr to save before starting, e.g. from the AWS relay output.")
    peer_setup.add_argument("--namespace", default="", help="Rendezvous namespace to save with --relay-addr.")
    peer_setup.add_argument("--profile-name", default="", help="Profile name to save when --relay-addr is provided. Defaults to --relay/--relay-profile or default.")
    peer_setup_invite = peer_setup.add_mutually_exclusive_group()
    peer_setup_invite.add_argument("--invite", type=Path, help="Invite JSON to accept after relay startup.")
    peer_setup_invite.add_argument("--invite-code", default="", help="amo-peer-invite: code to accept after relay startup.")
    peer_setup.add_argument("--install-startup", action="store_true", help="Install OS startup entries for sidecar and peer-agent watch.")
    peer_setup.add_argument("--service-name", default="AMO Peer Netd")
    add_peer_netd_watch_service_args(peer_setup)
    peer_setup.add_argument("--no-start", action="store_true", help="Only save config/profile; do not start peer-netd now.")
    peer_enable = peer_sub.add_parser("enable", help="Build if needed and start the managed libp2p sidecar")
    add_peer_netd_start_args(peer_enable)
    peer_netd = peer_sub.add_parser("netd", help="Build, start, stop, and inspect the managed libp2p sidecar")
    peer_netd_sub = peer_netd.add_subparsers(dest="netd_command", required=True)
    peer_netd_build = peer_netd_sub.add_parser("build", help="Compile amo-peer-netd into AMO_HOME/.peer/bin")
    peer_netd_build.add_argument("--out", type=Path, help="Optional output binary path.")
    peer_netd_start = peer_netd_sub.add_parser("start", help="Start the managed libp2p sidecar")
    add_peer_netd_start_args(peer_netd_start)
    peer_netd_sub.add_parser("stop", help="Stop the managed libp2p sidecar")
    peer_netd_sub.add_parser("status", help="Show managed libp2p sidecar process and health state")
    peer_netd_install = peer_netd_sub.add_parser("install-service", help="Plan or install OS startup for peer netd")
    add_peer_netd_start_args(peer_netd_install)
    peer_netd_install.add_argument("--service-name", default="AMO Peer Netd")
    add_peer_netd_watch_service_args(peer_netd_install)
    peer_netd_install.add_argument("--apply", action="store_true", help="Actually create the OS startup entry.")
    peer_netd_uninstall = peer_netd_sub.add_parser("uninstall-service", help="Plan or remove OS startup for peer netd")
    peer_netd_uninstall.add_argument("--service-name", default="AMO Peer Netd")
    add_peer_netd_watch_service_args(peer_netd_uninstall)
    peer_netd_uninstall.add_argument("--apply", action="store_true", help="Actually remove the OS startup entry.")
    peer_netd_service_status_cmd = peer_netd_sub.add_parser("service-status", help="Inspect the OS startup entry for peer netd")
    peer_netd_service_status_cmd.add_argument("--service-name", default="AMO Peer Netd")
    add_peer_netd_watch_service_args(peer_netd_service_status_cmd)
    peer_relay = peer_sub.add_parser("relay", help="Run or inspect a public AMO relay+rendezvous helper node")
    peer_relay_sub = peer_relay.add_subparsers(dest="relay_command", required=True)
    peer_relay_start = peer_relay_sub.add_parser("start", help="Start a combined circuit relay and rendezvous node")
    peer_relay_start.add_argument("--node-id", default="amo-relay")
    peer_relay_start.add_argument("--listen", default="/ip4/0.0.0.0/tcp/4001")
    peer_relay_start.add_argument("--api", default="127.0.0.1:8798")
    peer_relay_start.add_argument("--advertise-addr", action="append", default=[], help="Public libp2p multiaddr, e.g. /ip4/1.2.3.4/tcp/4001")
    peer_relay_start.add_argument("--namespace", default="amo-peer-default", help="Suggested rendezvous namespace for this trust group.")
    peer_relay_start.add_argument("--store-path", default="")
    peer_relay_start.add_argument("--no-build", action="store_true")
    peer_relay_save = peer_relay_sub.add_parser("save", help="Save a client relay profile for short --relay commands")
    peer_relay_save.add_argument("--name", required=True)
    peer_relay_save.add_argument("--addr", required=True, help="Relay/rendezvous multiaddr.")
    peer_relay_save.add_argument("--rendezvous-addr", default="", help="Optional distinct rendezvous multiaddr. Defaults to --addr.")
    peer_relay_save.add_argument("--namespace", required=True, help="Rendezvous namespace for this trust group.")
    peer_relay_save.add_argument("--no-auto-relay", action="store_true", help="Do not enable AutoRelay when this profile is used.")
    peer_relay_save.add_argument("--no-hole-punching", action="store_true", help="Do not enable hole punching when this profile is used.")
    peer_relay_show = peer_relay_sub.add_parser("show", help="Show one saved client relay profile")
    peer_relay_show.add_argument("--name", required=True)
    peer_relay_delete = peer_relay_sub.add_parser("delete", help="Delete one saved client relay profile")
    peer_relay_delete.add_argument("--name", required=True)
    peer_relay_sub.add_parser("list", help="List saved client relay profiles")
    peer_relay_sub.add_parser("status", help="Show the managed relay/rendezvous node status")
    peer_poll_netd = peer_sub.add_parser("poll-netd", help="Process delivered sidecar messages into local peer rooms")
    peer_poll_netd.add_argument("--limit", type=int, default=None)
    peer_poll_netd.add_argument("--watch", action="store_true", help="Keep polling the sidecar inbox until interrupted.")
    peer_poll_netd.add_argument("--interval-seconds", type=float, default=2.0)
    peer_poll_netd.add_argument("--max-iterations", type=int, default=0, help="Testing/debug guard for --watch. 0 means forever.")
    peer_poll_netd.add_argument("--fail-fast", action="store_true", help="In watch mode, exit on the first poll error.")
    peer_serve = peer_sub.add_parser("serve", help="Run the direct peer listener for Tailscale/private networking")
    peer_serve.add_argument("--host", default="0.0.0.0")
    peer_serve.add_argument("--port", type=int, default=8787)

    peer_agent = sub.add_parser("peer-agent", help="Run AMO peer-agent ask/watch/finalize workflows")
    peer_agent.add_argument("--amo-home", type=Path, help="AMO home directory for peer-agent state.")
    peer_agent_sub = peer_agent.add_subparsers(dest="peer_agent_command", required=True)
    peer_agent_ask = peer_agent_sub.add_parser("ask", help="Ask local memory first, then open a peer room if needed")
    peer_agent_ask.add_argument("--query", required=True)
    peer_agent_ask.add_argument("--peer", action="append", default=[], help="Trusted peer node id to ask. Repeat for multiple peers.")
    peer_agent_ask.add_argument("--session-id", default="")
    peer_agent_ask.add_argument("--min-confidence", type=float, default=None)
    peer_agent_ask.add_argument("--timeout-seconds", type=float, default=None)
    peer_agent_watch = peer_agent_sub.add_parser("watch", help="Drain peer inbox and respond/finalize rooms")
    peer_agent_watch.add_argument("--interval-seconds", type=float, default=2.0)
    peer_agent_watch.add_argument("--max-iterations", type=int, default=0, help="Testing/debug guard. 0 means forever.")
    peer_agent_watch.add_argument("--limit", type=int, default=None, help="Maximum netd envelopes to drain per tick.")
    peer_agent_watch.add_argument("--fail-fast", action="store_true")
    peer_agent_status = peer_agent_sub.add_parser("status", help="Show peer-agent room state")
    peer_agent_status.add_argument("--room-id", required=True)
    peer_agent_context = peer_agent_sub.add_parser("context", help="Show local peer-agent room context")
    peer_agent_context.add_argument("--room-id", required=True)
    peer_agent_messages = peer_agent_sub.add_parser("messages", help="Show peer-agent room messages")
    peer_agent_messages.add_argument("--room-id", required=True)
    peer_agent_summary = peer_agent_sub.add_parser("summarize", help="Update an initiator-owned room summary")
    peer_agent_summary.add_argument("--room-id", required=True)


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


def _watch_peer_netd_inbox(
    svc: PeerService,
    *,
    limit: int | None,
    interval_seconds: float,
    max_iterations: int = 0,
    fail_fast: bool = False,
    emit_line: Callable[[object], None],
) -> int:
    if interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    iterations = 0
    try:
        while True:
            try:
                emit_line(svc.process_netd_inbox(limit=limit))
            except Exception as exc:
                emit_line({"ok": False, "error": str(exc), "watching": not fail_fast})
                if fail_fast:
                    return 1
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return 0
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        emit_line({"ok": True, "stopped": True, "reason": "interrupted"})
        return 0


def _watch_peer_agent(
    svc: PeerAgentService,
    *,
    limit: int | None,
    interval_seconds: float,
    max_iterations: int = 0,
    fail_fast: bool = False,
    emit_line: Callable[[object], None],
) -> int:
    if interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    iterations = 0
    try:
        while True:
            result = svc.watch_once(limit=limit)
            emit_line(result)
            if fail_fast and not result.get("ok"):
                return 1
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return 0
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        emit_line({"ok": True, "stopped": True, "reason": "interrupted"})
        return 0


def handle_peer_command(
    args: argparse.Namespace,
    *,
    emit: Callable[[object], None],
    emit_line: Callable[[object], None],
) -> int | None:
    """Run peer and peer-agent CLI commands."""
    if args.command == "peer":
        if args.peer_command == "serve":
            peer_args = ["--host", args.host, "--port", str(args.port)]
            if args.amo_home:
                peer_args.extend(["--amo-home", str(args.amo_home)])
            return peer_server_main(peer_args)
        if args.amo_home:
            os.environ["AMO_HOME"] = str(args.amo_home)
        settings = Settings.load()
        if args.peer_command == "enable":
            runtime = PeerNetdRuntime(settings)
            emit(
                runtime.start(
                    peer_netd_options_from_args(args, settings),
                    build_if_missing=not args.no_build,
                )
            )
            return 0
        if args.peer_command == "setup":
            store = PeerStore(settings)
            saved_profile = None
            invite = peer_invite_from_setup_args(args)
            if invite and not args.relay_addr and not args.relay_profile:
                card = invite.get("card") if isinstance(invite.get("card"), dict) else {}
                relay_addr = str(card.get("rendezvous_addr") or "").strip()
                namespace = str(card.get("rendezvous_namespace") or "").strip()
                if relay_addr and namespace:
                    saved_profile = store.save_relay_profile(
                        name=args.profile_name or namespace,
                        relay_addr=relay_addr,
                        rendezvous_addr=relay_addr,
                        rendezvous_namespace=namespace,
                    )
                    args.relay_profile = str(saved_profile["name"])
            if args.relay_addr:
                profile_name = args.profile_name or args.relay_profile or "default"
                saved_profile = store.save_relay_profile(
                    name=profile_name,
                    relay_addr=args.relay_addr,
                    rendezvous_addr=args.rendezvous_addr,
                    rendezvous_namespace=args.namespace or args.rendezvous_namespace,
                )
                args.relay_profile = profile_name
            if args.relay_profile:
                relay_profile = store.get_relay_profile(args.relay_profile)
                if not args.rendezvous_addr:
                    args.rendezvous_addr = str(relay_profile.get("rendezvous_addr") or relay_profile.get("relay_addr") or "")
                if not args.rendezvous_namespace:
                    args.rendezvous_namespace = str(relay_profile.get("rendezvous_namespace") or "")
                if not args.static_relay:
                    args.static_relay = [str(relay_profile.get("relay_addr") or "")]
                if relay_profile.get("auto_relay", True):
                    args.auto_relay = True
                if relay_profile.get("hole_punching", True):
                    args.hole_punching = True
            svc = PeerService(settings)
            init_result = svc.init_node(
                node_id=args.node_id,
                display_name=args.display_name,
                capabilities=args.capability or None,
            )
            launch = peer_netd_options_from_args(args, settings)
            netd_result = None if args.no_start else PeerNetdRuntime(settings).start(launch, build_if_missing=not args.no_build)
            accept_result = svc.accept_peer_invite(invite) if invite else None
            startup_result = None
            if args.install_startup:
                startup_result = install_peer_netd_service(
                    settings,
                    launch,
                    PeerNetdServiceOptions(
                        service_name=args.service_name,
                        apply=True,
                        with_watcher=True,
                        watch_service_name=args.watch_service_name,
                    ),
                )
            setup_ok = all(item is None or bool(item.get("ok")) for item in (init_result, netd_result, accept_result, startup_result))
            emit(
                {
                    "ok": setup_ok,
                    "init": init_result,
                    "relay_profile": saved_profile or (store.get_relay_profile(args.relay_profile) if args.relay_profile else None),
                    "netd": netd_result,
                    "accept_invite": accept_result,
                    "startup": startup_result,
                    "next_commands": peer_setup_next_commands(args),
                }
            )
            return 0 if setup_ok else 1
        if args.peer_command == "netd":
            runtime = PeerNetdRuntime(settings)
            if args.netd_command == "build":
                emit(runtime.build(args.out))
                return 0
            if args.netd_command == "start":
                emit(
                    runtime.start(
                        peer_netd_options_from_args(args, settings),
                        build_if_missing=not args.no_build,
                    )
                )
                return 0
            if args.netd_command == "stop":
                emit(runtime.stop())
                return 0
            if args.netd_command == "status":
                emit(runtime.status())
                return 0
            if args.netd_command == "install-service":
                emit(
                    install_peer_netd_service(
                        settings,
                        peer_netd_options_from_args(args, settings),
                        peer_netd_service_options_from_args(args),
                    )
                )
                return 0
            if args.netd_command == "uninstall-service":
                emit(
                    uninstall_peer_netd_service(
                        settings,
                        peer_netd_service_options_from_args(args),
                    )
                )
                return 0
            if args.netd_command == "service-status":
                result = peer_netd_service_status(peer_netd_service_options_from_args(args))
                emit(result)
                return 0 if result.get("ok") else 1
        if args.peer_command == "relay":
            runtime = PeerNetdRuntime(settings)
            if args.relay_command == "start":
                result = runtime.start(
                    peer_relay_options_from_args(args),
                    build_if_missing=not args.no_build,
                )
                emit(with_relay_next_steps(result, args.namespace))
                return 0
            if args.relay_command == "save":
                profile = PeerStore(settings).save_relay_profile(
                    name=args.name,
                    relay_addr=args.addr,
                    rendezvous_addr=args.rendezvous_addr,
                    rendezvous_namespace=args.namespace,
                    auto_relay=not args.no_auto_relay,
                    hole_punching=not args.no_hole_punching,
                )
                emit(
                    {
                        "ok": True,
                        "profile": profile,
                        "next_commands": [
                            f"amo-cli peer enable --node-id <device-node-id> --relay {profile['name']}",
                            f"amo-cli peer create-invite --auto-approve --relay {profile['name']} --out host.invite.json",
                        ],
                    }
                )
                return 0
            if args.relay_command == "list":
                store = PeerStore(settings)
                emit({"ok": True, "profiles": store.list_relay_profiles(), "path": str(store.relay_profiles_path)})
                return 0
            if args.relay_command == "show":
                emit({"ok": True, "profile": PeerStore(settings).get_relay_profile(args.name)})
                return 0
            if args.relay_command == "delete":
                emit(PeerStore(settings).delete_relay_profile(args.name))
                return 0
            if args.relay_command == "status":
                emit(runtime.status())
                return 0
        if args.peer_command == "doctor":
            result = peer_doctor(settings)
            emit(result)
            return 0 if result.get("ready") or not args.strict else 1
        svc = PeerService(settings)
        if args.peer_command == "init":
            emit(
                svc.init_node(
                    node_id=args.node_id,
                    display_name=args.display_name,
                    capabilities=args.capability or None,
                )
            )
            return 0
        if args.peer_command == "add":
            emit(
                svc.add_peer(
                    node_id=args.node_id,
                    base_url=args.base_url,
                    peer_id=args.peer_id,
                    multiaddrs=args.multiaddr,
                    relay_addrs=args.relay_addr,
                    rendezvous_addr=args.rendezvous_addr,
                    rendezvous_namespace=args.rendezvous_namespace,
                    display_name=args.display_name,
                    capabilities=args.capability or None,
                    trust=args.trust,
                    shared_secret_env=args.shared_secret_env,
                )
            )
            return 0
        if args.peer_command == "remove":
            emit(svc.store.remove_peer(args.node_id))
            return 0
        if args.peer_command == "status":
            emit(svc.status())
            return 0
        if args.peer_command == "share-card":
            relay_values = relay_values_from_args(args, settings)
            result = svc.share_card(
                base_url=args.base_url,
                rendezvous_addr=relay_values["rendezvous_addr"],
                rendezvous_namespace=relay_values["rendezvous_namespace"],
            )
            if result.get("ok") and args.out:
                args.out.parent.mkdir(parents=True, exist_ok=True)
                args.out.write_text(json.dumps(result["card"], indent=2), encoding="utf-8")
                result = result | {"path": str(args.out.resolve())}
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_command == "import-card":
            card = json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(card, dict):
                raise ValueError("peer card file must contain a JSON object")
            emit(svc.import_card(card, trust=args.trust, shared_secret_env=args.shared_secret_env))
            return 0
        if args.peer_command == "create-invite":
            relay_values = relay_values_from_args(args, settings)
            result = svc.create_peer_invite(
                trust=args.trust,
                shared_secret_env=args.shared_secret_env,
                label=args.label,
                base_url=args.base_url,
                rendezvous_addr=relay_values["rendezvous_addr"],
                rendezvous_namespace=relay_values["rendezvous_namespace"],
                auto_approve=args.auto_approve,
                expires_minutes=args.expires_minutes,
                max_uses=args.max_uses,
            )
            if result.get("ok") and args.out:
                args.out.write_text(json.dumps(result["invite"], indent=2), encoding="utf-8")
                result["out"] = str(args.out)
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_command == "accept-invite":
            invite = decode_invite_code(args.code) if args.code else json.loads(args.file.read_text(encoding="utf-8"))
            if not isinstance(invite, dict):
                raise ValueError("peer invite must contain a JSON object")
            result = svc.accept_peer_invite(
                invite,
                trust=args.trust,
                shared_secret_env=args.shared_secret_env,
                send_join_request=not args.no_send_join_request,
            )
            if result.get("ok") and args.response_out and result.get("response_card"):
                args.response_out.write_text(json.dumps(result["response_card"], indent=2), encoding="utf-8")
                result["response_out"] = str(args.response_out)
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_command == "join-requests":
            emit(svc.list_join_requests(status=args.status))
            return 0
        if args.peer_command == "approve-join":
            result = svc.approve_join_request(args.request_id)
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_command == "reject-join":
            result = svc.reject_join_request(args.request_id, reason=args.reason)
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_command == "rooms":
            emit(svc.list_rooms())
            return 0
        if args.peer_command == "context":
            emit(svc.context_pack(args.room_id, viewer_node_id=args.viewer_node_id or None))
            return 0
        if args.peer_command == "append-message":
            emit(
                svc.append_message(
                    room_id=args.room_id,
                    from_node_id=args.from_node_id,
                    to_node_ids=args.to_node_id,
                    message_type=args.type,
                    content=args.content,
                    citations=args.citation,
                    confidence=args.confidence,
                )
            )
            return 0
        if args.peer_command == "send-message":
            emit(
                svc.send_message_to_peer(
                    peer_id=args.peer_id,
                    room_id=args.room_id,
                    content=args.content,
                    message_type=args.type,
                    citations=args.citation,
                    confidence=args.confidence,
                )
            )
            return 0
        if args.peer_command == "update-summary":
            emit(svc.update_summary(args.room_id, summary_md=args.summary))
            return 0
        if args.peer_command == "open-room":
            emit(svc.open_room(topic=args.topic, peer_ids=args.peer, send_invites=not args.no_send))
            return 0
        if args.peer_command == "poll-netd":
            if args.watch:
                return _watch_peer_netd_inbox(
                    svc,
                    limit=args.limit,
                    interval_seconds=args.interval_seconds,
                    max_iterations=args.max_iterations,
                    fail_fast=args.fail_fast,
                    emit_line=emit_line,
                )
            emit(svc.process_netd_inbox(limit=args.limit))
            return 0
        return None

    if args.command == "peer-agent":
        if args.amo_home:
            os.environ["AMO_HOME"] = str(args.amo_home)
        settings = Settings.load()
        svc = PeerAgentService(settings)
        if args.peer_agent_command == "ask":
            result = svc.ask(
                query=args.query,
                peer_ids=args.peer or None,
                session_id=args.session_id,
                min_confidence=args.min_confidence,
                timeout_seconds=args.timeout_seconds,
            )
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_agent_command == "watch":
            return _watch_peer_agent(
                svc,
                limit=args.limit,
                interval_seconds=args.interval_seconds,
                max_iterations=args.max_iterations,
                fail_fast=args.fail_fast,
                emit_line=emit_line,
            )
        if args.peer_agent_command == "status":
            emit(svc.status(args.room_id))
            return 0
        if args.peer_agent_command == "context":
            emit(svc.context(args.room_id))
            return 0
        if args.peer_agent_command == "messages":
            emit(svc.messages(args.room_id))
            return 0
        if args.peer_agent_command == "summarize":
            emit(svc.summarize(args.room_id))
            return 0
        return None

    return None


__all__ = [
    "add_peer_netd_start_args",
    "add_peer_netd_watch_service_args",
    "handle_peer_command",
    "peer_invite_from_setup_args",
    "peer_netd_options_from_args",
    "peer_netd_service_options_from_args",
    "peer_relay_options_from_args",
    "peer_setup_next_commands",
    "relay_values_from_args",
    "with_relay_next_steps",
]
