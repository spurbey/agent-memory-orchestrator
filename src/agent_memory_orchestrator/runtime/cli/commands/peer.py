from __future__ import annotations

import argparse
import json
import os
import time
from collections.abc import Callable
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
