from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from ..core.config import Settings
from .auth import PeerAuthError
from .cards import build_peer_card, peer_from_card
from .invites import build_peer_invite
from .invites import encode_invite_code
from .invites import invite_token_hash
from .invites import parse_peer_invite
from .invites import peer_card_sha256
from .invites import verify_invite_token_proof
from .models import PeerNode
from .netd_client import PeerNetdClient, PeerNetdError
from .netd_runtime import PeerNetdRuntime
from .netd_transport import build_netd_raw_message
from .netd_transport import connect_peer_via_netd
from .netd_transport import normalize_netd_envelope
from .netd_transport import post_json
from .policy import PeerPolicy
from .protocol import PeerMessage
from .store import PeerStore
from .transport_auth import enforce_transport_auth
from .transport_auth import prepare_outgoing_payload
from .transport_auth import unwrap_incoming_payload
from .service_utils import _netd_envelope_id
from .service_utils import _parse_datetime
from .service_utils import _utc_now
from .service_utils import _with_transport_auth_metadata


class PeerService:
    def __init__(
        self,
        settings: Settings,
        store: PeerStore | None = None,
        netd_client: PeerNetdClient | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or PeerStore(settings)
        self.netd_client = netd_client

    def init_node(self, *, node_id: str, display_name: str = "", capabilities: list[str] | None = None) -> dict[str, Any]:
        config = self.store.init_config(
            node_id=node_id,
            display_name=display_name,
            capabilities=tuple(capabilities or ("graph_retrieval", "memory_search")),
        )
        return {"ok": True, "config_path": str(self.store.config_path), "peer": config.to_dict()}

    def add_peer(
        self,
        *,
        node_id: str,
        base_url: str = "",
        peer_id: str = "",
        multiaddrs: list[str] | None = None,
        relay_addrs: list[str] | None = None,
        rendezvous_addr: str = "",
        rendezvous_namespace: str = "",
        display_name: str = "",
        capabilities: list[str] | None = None,
        trust: str = "trusted",
        shared_secret_env: str = "",
    ) -> dict[str, Any]:
        config = self.store.add_peer(
            PeerNode(
                node_id=node_id,
                base_url=base_url,
                peer_id=peer_id,
                multiaddrs=tuple(multiaddrs or ()),
                relay_addrs=tuple(relay_addrs or ()),
                rendezvous_addr=rendezvous_addr,
                rendezvous_namespace=rendezvous_namespace,
                display_name=display_name,
                capabilities=tuple(capabilities or ()),
                trust=trust,
                shared_secret_env=shared_secret_env,
            )
        )
        return {"ok": True, "peer": config.peer_by_id(node_id).to_dict() if config.peer_by_id(node_id) else None}

    def status(self) -> dict[str, Any]:
        config = self.store.load_config()
        rooms = self.store.list_rooms()
        return {
            "ok": True,
            "node": {
                "node_id": config.node_id,
                "display_name": config.display_name,
                "transport": config.transport,
                "capabilities": list(config.capabilities),
                "auto_join": config.auto_join,
                "share_boundary": config.share_boundary(),
            },
            "config_path": str(self.store.config_path),
            "rooms_dir": str(self.store.rooms_dir),
            "peers": [peer.to_dict() for peer in config.peers],
            "relay_profiles": self.store.list_relay_profiles(),
            "room_count": len(rooms),
        }

    def share_card(
        self,
        *,
        base_url: str = "",
        rendezvous_addr: str = "",
        rendezvous_namespace: str = "",
    ) -> dict[str, Any]:
        config = self.store.load_config()
        netd_health = None
        try:
            if self.netd_client is not None:
                netd_health = self.netd_client.health()
            else:
                runtime_status = PeerNetdRuntime(self.settings).status()
                if runtime_status.get("api_ok"):
                    netd_health = runtime_status.get("health")
        except Exception:
            netd_health = None
        card = build_peer_card(
            config=config,
            netd_health=netd_health if isinstance(netd_health, dict) else None,
            base_url=base_url,
            rendezvous_addr=rendezvous_addr,
            rendezvous_namespace=rendezvous_namespace,
        )
        if not any((card["base_url"], card["peer_id"], card["multiaddrs"], card["relay_addrs"], card["rendezvous_addr"])):
            return {"ok": False, "error": "no usable peer address available; start peer netd or pass --base-url"}
        return {"ok": True, "card": card}

    def import_card(self, card: dict[str, Any], *, trust: str = "trusted", shared_secret_env: str = "") -> dict[str, Any]:
        peer = peer_from_card(card, trust=trust, shared_secret_env=shared_secret_env)
        config = self.store.add_peer(peer)
        saved = config.peer_by_id(peer.node_id)
        return {"ok": True, "peer": saved.to_dict() if saved else None}

    def create_peer_invite(
        self,
        *,
        trust: str = "trusted",
        shared_secret_env: str = "",
        label: str = "",
        base_url: str = "",
        rendezvous_addr: str = "",
        rendezvous_namespace: str = "",
        auto_approve: bool = False,
        expires_minutes: int = 1440,
        max_uses: int = 1,
    ) -> dict[str, Any]:
        card_result = self.share_card(
            base_url=base_url,
            rendezvous_addr=rendezvous_addr,
            rendezvous_namespace=rendezvous_namespace,
        )
        if not card_result.get("ok"):
            return card_result
        invite = build_peer_invite(
            card=card_result["card"],
            trust=trust,
            shared_secret_env=shared_secret_env,
            label=label,
            auto_approve=auto_approve,
            expires_minutes=expires_minutes,
            max_uses=max_uses,
        )
        self.store.save_peer_invite_record(
            {
                "invite_id": invite["invite_id"],
                "created_at": invite["created_at"],
                "expires_at": invite["expires_at"],
                "created_by_node_id": invite["created_by_node_id"],
                "label": invite["label"],
                "recommended_trust": invite["recommended_trust"],
                "shared_secret_env": invite["shared_secret_env"],
                "auto_approve": invite["auto_approve"],
                "max_uses": invite["max_uses"],
                "used_count": 0,
                "status": "pending",
                "token_hash": invite_token_hash(str(invite["invite_token"])),
                "card_sha256": invite["card_sha256"],
            }
        )
        return {
            "ok": True,
            "invite": invite,
            "invite_code": encode_invite_code(invite),
            "next_step": "Send invite_code or the invite JSON to the peer. The peer runs peer accept-invite.",
        }

    def accept_peer_invite(
        self,
        invite: dict[str, Any],
        *,
        trust: str = "",
        shared_secret_env: str = "",
        send_join_request: bool = True,
    ) -> dict[str, Any]:
        parsed = parse_peer_invite(invite)
        effective_trust = trust.strip() or str(parsed["trust"])
        effective_secret_env = shared_secret_env.strip() or str(parsed["shared_secret_env"])
        imported = self.import_card(parsed["card"], trust=effective_trust, shared_secret_env=effective_secret_env)
        response_card = None
        response_error = ""
        try:
            response = self.share_card()
            if response.get("ok"):
                response_card = response.get("card")
            else:
                response_error = str(response.get("error") or "could not create response card")
        except Exception as exc:
            response_error = str(exc)
        join_request_delivery = None
        if send_join_request and response_card and parsed.get("invite_token"):
            peer = self.store.load_config().peer_by_id(str(imported.get("peer", {}).get("node_id") or ""))
            if peer is not None and peer.peer_id:
                join_request_delivery = self._send_payload_via_netd(
                    peer,
                    {
                        "type": "peer_join_request",
                        "invite_id": parsed["invite_id"],
                        "token_proof": parsed["token_proof"],
                        "peer_card": response_card,
                        "peer_card_sha256": peer_card_sha256(response_card),
                        "requested_trust": effective_trust,
                    },
                    message_type="peer_join_request",
                    room_id="",
                )
        return {
            "ok": True,
            "imported_peer": imported.get("peer"),
            "card_sha256": parsed["card_sha256"],
            "response_card": response_card,
            "response_card_error": response_error,
            "join_request_delivery": join_request_delivery,
            "next_step": (
                "Join request sent to inviter."
                if join_request_delivery and join_request_delivery.get("ok")
                else "Return response_card to the inviter or ensure both sidecars are running for auto-handshake."
            ),
        }

    def receive_join_request(
        self,
        payload: dict[str, Any],
        *,
        transport_auth: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            invite_id = str(payload.get("invite_id") or "").strip()
            token_proof = str(payload.get("token_proof") or "").strip()
            peer_card = payload.get("peer_card")
            if not invite_id:
                return {"ok": False, "accepted": False, "error": "invite_id is required", "auth": transport_auth or {}}
            if not isinstance(peer_card, dict):
                return {"ok": False, "accepted": False, "error": "peer_card is required", "auth": transport_auth or {}}
            provided_card_hash = str(payload.get("peer_card_sha256") or "").strip()
            actual_card_hash = peer_card_sha256(peer_card)
            if provided_card_hash and provided_card_hash != actual_card_hash:
                return {
                    "ok": False,
                    "accepted": False,
                    "error": "peer_card hash mismatch",
                    "auth": transport_auth or {},
                }
            record = self.store.get_peer_invite_record(invite_id)
            validation = self._validate_join_request(record=record, token_proof=token_proof, peer_card=peer_card)
            if not validation.get("ok"):
                return validation | {"auth": transport_auth or {}}
            requested_trust = str(payload.get("requested_trust") or record.get("recommended_trust") or "trusted")
            if record.get("auto_approve"):
                imported = self.import_card(
                    peer_card,
                    trust=requested_trust,
                    shared_secret_env=str(record.get("shared_secret_env") or ""),
                )
                updated_invite = self._mark_invite_used(record)
                delivery = self._send_join_accepted(imported.get("peer"), invite_id)
                return {
                    "ok": True,
                    "accepted": True,
                    "mode": "auto_approved",
                    "peer": imported.get("peer"),
                    "invite": updated_invite,
                    "join_accepted_delivery": delivery,
                    "auth": transport_auth or {},
                }
            request_record = self.store.save_join_request(
                {
                    "invite_id": invite_id,
                    "peer_card": peer_card,
                    "peer_card_sha256": actual_card_hash,
                    "token_proof": token_proof,
                    "requested_trust": requested_trust,
                    "status": "pending",
                    "auth": transport_auth or {},
                }
            )
            return {
                "ok": True,
                "accepted": False,
                "mode": "pending_approval",
                "request": request_record,
                "auth": transport_auth or {},
            }
        except (FileNotFoundError, ValueError, PermissionError) as exc:
            return {"ok": False, "accepted": False, "error": str(exc), "auth": transport_auth or {}}

    def list_join_requests(self, *, status: str = "") -> dict[str, Any]:
        return {"ok": True, "requests": self.store.list_join_requests(status=status)}

    def approve_join_request(self, request_id: str) -> dict[str, Any]:
        request_record = self.store.get_join_request(request_id)
        if request_record.get("status") != "pending":
            return {"ok": False, "error": f"join request is not pending: {request_record.get('status')}"}
        invite = self.store.get_peer_invite_record(str(request_record.get("invite_id") or ""))
        validation = self._validate_join_request(
            record=invite,
            token_proof=str(request_record.get("token_proof") or invite.get("token_hash") or ""),
            peer_card=request_record.get("peer_card"),
            allow_stored_token_hash=True,
        )
        if not validation.get("ok"):
            return validation
        expected_card_hash = str(request_record.get("peer_card_sha256") or "")
        actual_card_hash = peer_card_sha256(request_record["peer_card"])
        if expected_card_hash and expected_card_hash != actual_card_hash:
            return {"ok": False, "error": "peer_card hash mismatch"}
        imported = self.import_card(
            request_record["peer_card"],
            trust=str(request_record.get("requested_trust") or invite.get("recommended_trust") or "trusted"),
            shared_secret_env=str(invite.get("shared_secret_env") or ""),
        )
        updated_invite = self._mark_invite_used(invite)
        updated_request = self.store.update_join_request(request_id, {"status": "approved", "approved_at": _utc_now()})
        delivery = self._send_join_accepted(imported.get("peer"), str(invite.get("invite_id") or ""))
        return {
            "ok": True,
            "request": updated_request,
            "peer": imported.get("peer"),
            "invite": updated_invite,
            "join_accepted_delivery": delivery,
        }

    def reject_join_request(self, request_id: str, *, reason: str = "") -> dict[str, Any]:
        request_record = self.store.get_join_request(request_id)
        updated = self.store.update_join_request(
            request_id,
            {"status": "rejected", "rejected_at": _utc_now(), "reject_reason": reason.strip()},
        )
        return {"ok": True, "request": updated, "previous_status": request_record.get("status")}

    def capabilities(self) -> dict[str, Any]:
        config = self.store.load_config()
        return {
            "ok": True,
            "node_id": config.node_id,
            "display_name": config.display_name,
            "transport": config.transport,
            "capabilities": list(config.capabilities),
            "share_boundary": config.share_boundary(),
        }

    def open_room(self, *, topic: str, peer_ids: list[str], send_invites: bool = True) -> dict[str, Any]:
        config = self.store.load_config()
        room = self.store.create_room(topic=topic, participants=peer_ids, share_boundary=config.share_boundary())
        deliveries = []
        if send_invites:
            for peer_id in peer_ids:
                peer = config.peer_by_id(peer_id)
                if peer is None:
                    deliveries.append({"peer_id": peer_id, "ok": False, "error": "peer not configured"})
                    continue
                deliveries.append(self.send_invite(peer, room["room_id"]))
        return {"ok": True, "room": room, "deliveries": deliveries}

    def send_invite(self, peer: PeerNode, room_id: str) -> dict[str, Any]:
        if peer.peer_id:
            payload = self.store.invite_payload(room_id)
            return self._send_payload_via_netd(peer, payload, message_type="room_invite", room_id=room_id)
        if not peer.base_url:
            return {"peer_id": peer.node_id, "ok": False, "error": "peer has no libp2p peer_id or legacy base_url"}
        payload = self.store.invite_payload(room_id)
        prepared = prepare_outgoing_payload(peer, payload, config=self.store.load_config())
        if not prepared.get("ok"):
            return {"peer_id": peer.node_id, **prepared}
        result = post_json(f"{peer.base_url}/peer/rooms/invite", prepared["payload"])
        return {"peer_id": peer.node_id, "auth": prepared.get("auth", {}), **result}

    def receive_invite(self, payload: dict[str, Any], *, transport_auth: dict[str, Any] | None = None) -> dict[str, Any]:
        auth: dict[str, Any] = {}
        try:
            if transport_auth:
                auth = transport_auth
                self._enforce_transport_auth(
                    str(payload.get("initiator_node_id") or payload.get("initiator") or "").strip(),
                    auth,
                )
            else:
                payload, auth = unwrap_incoming_payload(payload, config=self.store.load_config())
            room = self.store.accept_invite(payload)
        except PeerAuthError as exc:
            return {"ok": False, "accepted": False, "error": str(exc), "auth": {"authenticated": False}}
        except PermissionError as exc:
            return {"ok": False, "accepted": False, "error": str(exc), "auth": auth}
        return {"ok": True, "accepted": True, "room": room, "auth": auth}

    def receive_message(self, payload: dict[str, Any], *, transport_auth: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            if transport_auth:
                auth = transport_auth
            else:
                payload, auth = unwrap_incoming_payload(payload, config=self.store.load_config())
            config = self.store.load_config()
            policy = PeerPolicy(config)
            if transport_auth:
                payload = _with_transport_auth_metadata(payload, transport_auth)
            message = PeerMessage.from_payload(payload)
            if not message.room_id:
                return {"ok": False, "error": "room_id is required", "auth": auth}
            self._enforce_transport_auth(
                message.from_node_id,
                auth,
                require_peer_binding=message.message_type in {"context_request", "context_response"},
            )
            room = self.store.get_room(message.room_id)
            decision = policy.decide_message(
                message.from_node_id,
                participants=[str(item) for item in room.get("participants", [])],
            )
            if not decision.allowed:
                return {"ok": False, "error": decision.reason, "auth": auth}
            stored = self.store.append_message(message.room_id, message.to_record())
            return {"ok": True, "message": stored, "auth": auth}
        except PeerAuthError as exc:
            return {"ok": False, "error": str(exc), "auth": {"authenticated": False}}
        except FileNotFoundError as exc:
            return {"ok": False, "error": str(exc), "auth": auth}

    def append_message(
        self,
        *,
        room_id: str,
        from_node_id: str,
        content: str,
        to_node_ids: list[str] | None = None,
        message_type: str = "context_request",
        citations: list[str] | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        message = PeerMessage(
            room_id=room_id,
            message_type=message_type,
            from_node_id=from_node_id,
            to_node_ids=tuple(to_node_ids or ()),
            content=content,
            citations=tuple(citations or ()),
            confidence=confidence,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        return {"ok": True, "message": self.store.append_message(room_id, message.to_record())}

    def send_message_to_peer(
        self,
        *,
        peer_id: str,
        room_id: str,
        content: str,
        message_type: str = "context_request",
        citations: list[str] | None = None,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        append_on_success_only: bool = False,
    ) -> dict[str, Any]:
        config = self.store.load_config()
        peer = config.peer_by_id(peer_id)
        if peer is None:
            return {"ok": False, "peer_id": peer_id, "error": "peer not configured"}
        message = PeerMessage(
            room_id=room_id,
            message_type=message_type,
            from_node_id=config.node_id,
            to_node_ids=(peer.node_id,),
            content=content,
            citations=tuple(citations or ()),
            confidence=confidence,
            metadata=metadata if isinstance(metadata, dict) else {},
        )
        record = message.to_record()
        payload = record | {"room_id": room_id}
        delivery = self._send_payload_via_netd(peer, payload, message_type=message_type, room_id=room_id)
        if append_on_success_only and not delivery.get("ok"):
            return {"ok": False, "message": record, "delivery": delivery}
        stored = self.store.append_message(room_id, record)
        return {"ok": bool(delivery.get("ok")), "message": stored, "delivery": delivery}

    def process_netd_inbox(self, limit: int | None = None) -> dict[str, Any]:
        client = self._netd()
        processed_ids = self.store.load_processed_netd_ids()
        results = []
        messages = client.messages()
        for envelope in messages[:limit]:
            envelope_id = _netd_envelope_id(envelope)
            if envelope_id in processed_ids:
                results.append({"ok": True, "skipped": True, "reason": "already_processed", "envelope_id": envelope_id})
                continue
            result = self.receive_netd_envelope(envelope)
            result["envelope_id"] = envelope_id
            if result.get("ok"):
                self.store.mark_processed_netd_id(envelope_id)
                processed_ids.add(envelope_id)
                result["processed"] = True
            else:
                result["processed"] = False
            results.append(result)
        return {"ok": True, "count": len(results), "results": results}

    def receive_netd_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_netd_envelope(envelope)
        if not normalized.get("ok"):
            return normalized
        message_type = str(normalized.get("message_type") or "")
        payload = normalized.get("payload") if isinstance(normalized.get("payload"), dict) else {}
        auth = normalized.get("auth") if isinstance(normalized.get("auth"), dict) else {}
        if message_type == "room_invite":
            return self.receive_invite(payload, transport_auth=auth)
        if message_type == "peer_join_request":
            return self.receive_join_request(payload, transport_auth=auth)
        if message_type == "peer_join_accepted":
            return {"ok": True, "accepted": True, "type": "peer_join_accepted", "payload": payload, "auth": auth}
        return self.receive_message(payload, transport_auth=auth)

    def _validate_join_request(
        self,
        *,
        record: dict[str, Any],
        token_proof: str,
        peer_card: Any,
        allow_stored_token_hash: bool = False,
    ) -> dict[str, Any]:
        if not isinstance(peer_card, dict):
            return {"ok": False, "accepted": False, "error": "peer_card is required"}
        status = str(record.get("status") or "pending")
        if status in {"revoked", "expired", "accepted"}:
            return {"ok": False, "accepted": False, "error": f"invite is {status}"}
        expires_at = _parse_datetime(str(record.get("expires_at") or ""))
        if expires_at and expires_at < datetime.now(timezone.utc):
            self.store.update_peer_invite_record(str(record.get("invite_id") or ""), {"status": "expired"})
            return {"ok": False, "accepted": False, "error": "invite is expired"}
        used_count = int(record.get("used_count") or 0)
        max_uses = max(1, int(record.get("max_uses") or 1))
        if used_count >= max_uses:
            return {"ok": False, "accepted": False, "error": "invite use limit reached"}
        token_hash = str(record.get("token_hash") or "")
        token_ok = verify_invite_token_proof(token_hash=token_hash, token_proof=token_proof)
        if allow_stored_token_hash and token_proof == token_hash:
            token_ok = True
        if not token_ok:
            return {"ok": False, "accepted": False, "error": "invite token proof mismatch"}
        existing = self.store.load_config().peer_by_id(str(peer_card.get("node_id") or ""))
        if existing is not None and existing.trust == "blocked":
            return {"ok": False, "accepted": False, "error": f"peer is blocked: {existing.node_id}"}
        return {"ok": True}

    def _mark_invite_used(self, record: dict[str, Any]) -> dict[str, Any]:
        used_count = int(record.get("used_count") or 0) + 1
        max_uses = max(1, int(record.get("max_uses") or 1))
        status = "accepted" if used_count >= max_uses else "pending"
        return self.store.update_peer_invite_record(
            str(record.get("invite_id") or ""),
            {"used_count": used_count, "status": status},
        )

    def _send_join_accepted(self, peer_payload: Any, invite_id: str) -> dict[str, Any] | None:
        if not isinstance(peer_payload, dict):
            return None
        peer = self.store.load_config().peer_by_id(str(peer_payload.get("node_id") or ""))
        if peer is None or not peer.peer_id:
            return None
        return self._send_payload_via_netd(
            peer,
            {
                "type": "peer_join_accepted",
                "invite_id": invite_id,
                "trusted_as": peer.trust,
            },
            message_type="peer_join_accepted",
            room_id="",
        )

    def list_rooms(self) -> dict[str, Any]:
        return {"ok": True, "rooms": self.store.list_rooms()}

    def room_detail(self, room_id: str) -> dict[str, Any]:
        return {"ok": True, "room": self.store.get_room(room_id)}

    def context_pack(self, room_id: str, *, viewer_node_id: str | None = None) -> dict[str, Any]:
        return {"ok": True, "context": self.store.context_pack(room_id, viewer_node_id=viewer_node_id)}

    def update_summary(self, room_id: str, *, summary_md: str) -> dict[str, Any]:
        return {"ok": True, "message": self.store.update_rolling_summary(room_id, summary_md)}

    def _enforce_transport_auth(
        self,
        sender_node_id: str,
        auth: dict[str, Any],
        *,
        require_peer_binding: bool = False,
    ) -> None:
        enforce_transport_auth(
            self.store.load_config(),
            sender_node_id,
            auth,
            require_peer_binding=require_peer_binding,
        )

    def _send_payload_via_netd(
        self,
        peer: PeerNode,
        payload: dict[str, Any],
        *,
        message_type: str,
        room_id: str,
    ) -> dict[str, Any]:
        if not peer.peer_id:
            return {"peer_id": peer.node_id, "ok": False, "error": "peer_id is required for libp2p send"}
        client = self._netd()
        connect_results = connect_peer_via_netd(peer, client)
        config = self.store.load_config()
        try:
            result = client.send_raw(
                peer.peer_id,
                build_netd_raw_message(
                    local_node_id=config.node_id,
                    peer=peer,
                    payload=payload,
                    message_type=message_type,
                    room_id=room_id,
                ),
            )
        except (PeerNetdError, ValueError, TypeError) as exc:
            return {
                "peer_id": peer.node_id,
                "ok": False,
                "transport": "libp2p",
                "connect": connect_results,
                "error": str(exc),
            }
        return {
            "peer_id": peer.node_id,
            "ok": bool(result.get("ok", True)),
            "transport": "libp2p",
            "connect": connect_results,
            "response": result,
        }

    def _netd(self) -> PeerNetdClient:
        if self.netd_client is not None:
            return self.netd_client
        env_url = os.getenv("AMO_PEER_NETD_URL", "").strip()
        if env_url:
            return PeerNetdClient(base_url=env_url)
        status = PeerNetdRuntime(self.settings).status()
        api_url = str(status.get("api_url") or "").strip()
        return PeerNetdClient(base_url=api_url or "http://127.0.0.1:8788")
