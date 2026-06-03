from __future__ import annotations

from typing import Any

from .auth import PeerAuthError, secret_for_peer, unwrap_payload, wrap_payload
from .models import PeerConfig, PeerNode


def prepare_outgoing_payload(peer: PeerNode, payload: dict[str, Any], *, config: PeerConfig) -> dict[str, Any]:
    if not peer.shared_secret_env:
        return {"ok": True, "payload": payload, "auth": {"signed": False}}
    secret = secret_for_peer(peer)
    if not secret:
        return {
            "ok": False,
            "error": f"shared secret env is not set for peer {peer.node_id}: {peer.shared_secret_env}",
        }
    return {
        "ok": True,
        "payload": wrap_payload(payload=payload, from_node_id=config.node_id, secret=secret),
        "auth": {"signed": True, "algorithm": "hmac-sha256"},
    }


def unwrap_incoming_payload(payload: dict[str, Any], *, config: PeerConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    return unwrap_payload(payload=payload, config=config)


def enforce_transport_auth(
    config: PeerConfig,
    sender_node_id: str,
    auth: dict[str, Any],
    *,
    require_peer_binding: bool = False,
) -> None:
    peer = config.peer_by_id(sender_node_id)
    if peer is not None and peer.shared_secret_env and not auth.get("authenticated"):
        raise PeerAuthError(f"signed envelope required for peer: {sender_node_id}")
    remote_peer_id = str(auth.get("remote_peer_id") or "").strip()
    if peer is not None and remote_peer_id and peer.peer_id and remote_peer_id != peer.peer_id:
        raise PeerAuthError(f"remote peer id mismatch for {sender_node_id}")
    is_netd = str(auth.get("auth") or "").startswith("netd:")
    if require_peer_binding and is_netd and peer is not None:
        if peer.peer_id and not remote_peer_id and not auth.get("authenticated"):
            raise PeerAuthError(f"remote peer id or signed envelope required for peer-agent message: {sender_node_id}")
        if not peer.peer_id and not auth.get("authenticated"):
            raise PeerAuthError(f"signed envelope required for peer-agent message without peer_id: {sender_node_id}")
