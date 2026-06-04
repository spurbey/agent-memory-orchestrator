from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable

from .....core.config import Settings
from .....peer import PeerService
from .....peer.agent import PeerAgentService
from .....peer.doctor import peer_doctor
from .....peer.invites import decode_invite_code
from .....peer.netd_runtime import PeerNetdRuntime
from .....peer.netd_service import PeerNetdServiceOptions
from .....peer.netd_service import install_service as install_peer_netd_service
from .....peer.netd_service import service_status as peer_netd_service_status
from .....peer.netd_service import uninstall_service as uninstall_peer_netd_service
from .....peer.server import main as peer_server_main
from .....peer.store import PeerStore

from .options import peer_invite_from_setup_args
from .options import peer_netd_options_from_args
from .options import peer_netd_service_options_from_args
from .options import peer_relay_options_from_args
from .options import peer_setup_next_commands
from .options import relay_values_from_args
from .options import with_relay_next_steps
from .watch import _watch_peer_agent
from .watch import _watch_peer_netd_inbox


def _root_compat_hook(name: str, default: Callable) -> Callable:
    peer_module = sys.modules.get(__package__)
    if peer_module is None:
        return default
    return getattr(peer_module, name, default)


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
            return _root_compat_hook("peer_server_main", peer_server_main)(peer_args)
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
                startup_result = _root_compat_hook("install_peer_netd_service", install_peer_netd_service)(
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
                    _root_compat_hook("install_peer_netd_service", install_peer_netd_service)(
                        settings,
                        peer_netd_options_from_args(args, settings),
                        peer_netd_service_options_from_args(args),
                    )
                )
                return 0
            if args.netd_command == "uninstall-service":
                emit(
                    _root_compat_hook("uninstall_peer_netd_service", uninstall_peer_netd_service)(
                        settings,
                        peer_netd_service_options_from_args(args),
                    )
                )
                return 0
            if args.netd_command == "service-status":
                result = _root_compat_hook("peer_netd_service_status", peer_netd_service_status)(
                    peer_netd_service_options_from_args(args)
                )
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
                    metadata={"audience": args.audience} if args.audience else None,
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
                    metadata={"audience": args.audience} if args.audience else None,
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
        if args.peer_agent_command == "ask-room":
            result = svc.ask_room(
                room_id=args.room_id,
                query=args.query,
                peer_ids=args.peer or None,
                session_id=args.session_id,
                min_confidence=args.min_confidence,
                timeout_seconds=args.timeout_seconds,
            )
            emit(result)
            return 0 if result.get("ok") else 1
        if args.peer_agent_command == "continue":
            result = svc.continue_room(
                room_id=args.room_id,
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
