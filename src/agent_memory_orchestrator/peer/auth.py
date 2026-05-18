from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .models import PeerConfig, PeerNode

ENVELOPE_VERSION = 1
SIGNATURE_PREFIX = "hmac-sha256:"


class PeerAuthError(ValueError):
    pass


def wrap_payload(*, payload: dict[str, Any], from_node_id: str, secret: str) -> dict[str, Any]:
    if not secret:
        raise PeerAuthError("shared secret is required")
    payload_hash = _payload_hash(payload)
    envelope = {
        "amo_peer_envelope_version": ENVELOPE_VERSION,
        "from_node_id": from_node_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "nonce": uuid4().hex,
        "payload_sha256": payload_hash,
        "payload": payload,
    }
    envelope["signature"] = SIGNATURE_PREFIX + _signature(envelope, secret)
    return envelope


def unwrap_payload(*, payload: dict[str, Any], config: PeerConfig) -> tuple[dict[str, Any], dict[str, Any]]:
    if _is_envelope(payload):
        sender = str(payload.get("from_node_id") or "").strip()
        peer = config.peer_by_id(sender)
        if peer is None:
            raise PeerAuthError(f"sender is not configured: {sender}")
        secret = secret_for_peer(peer)
        if not secret:
            raise PeerAuthError(f"shared secret env is not set for peer: {sender}")
        _verify_envelope(payload, secret)
        inner = payload.get("payload")
        if not isinstance(inner, dict):
            raise PeerAuthError("envelope payload must be a JSON object")
        inner_sender = sender_from_payload(inner)
        if inner_sender and inner_sender != sender:
            raise PeerAuthError(f"envelope sender mismatch: {sender} != {inner_sender}")
        return inner, {
            "authenticated": True,
            "auth": "hmac-sha256",
            "from_node_id": sender,
            "payload_sha256": payload.get("payload_sha256"),
        }

    sender = sender_from_payload(payload)
    peer = config.peer_by_id(sender) if sender else None
    if peer is not None and peer.shared_secret_env:
        raise PeerAuthError(f"signed envelope required for peer: {sender}")
    return payload, {"authenticated": False, "auth": "none", "from_node_id": sender}


def sender_from_payload(payload: dict[str, Any]) -> str:
    return str(
        payload.get("from_node_id")
        or payload.get("from")
        or payload.get("initiator_node_id")
        or payload.get("initiator")
        or ""
    ).strip()


def secret_for_peer(peer: PeerNode) -> str:
    return os.environ.get(peer.shared_secret_env, "") if peer.shared_secret_env else ""


def _is_envelope(payload: dict[str, Any]) -> bool:
    return payload.get("amo_peer_envelope_version") == ENVELOPE_VERSION and isinstance(payload.get("payload"), dict)


def _verify_envelope(envelope: dict[str, Any], secret: str) -> None:
    inner = envelope.get("payload")
    if not isinstance(inner, dict):
        raise PeerAuthError("envelope payload must be a JSON object")
    expected_hash = _payload_hash(inner)
    if not hmac.compare_digest(expected_hash, str(envelope.get("payload_sha256") or "")):
        raise PeerAuthError("payload hash mismatch")
    actual = str(envelope.get("signature") or "")
    expected = SIGNATURE_PREFIX + _signature(envelope, secret)
    if not hmac.compare_digest(actual, expected):
        raise PeerAuthError("signature mismatch")


def _signature(envelope: dict[str, Any], secret: str) -> str:
    signing_payload = {
        "amo_peer_envelope_version": envelope.get("amo_peer_envelope_version"),
        "from_node_id": envelope.get("from_node_id"),
        "created_at": envelope.get("created_at"),
        "nonce": envelope.get("nonce"),
        "payload_sha256": envelope.get("payload_sha256"),
    }
    return hmac.new(secret.encode("utf-8"), _canonical_json(signing_payload).encode("utf-8"), hashlib.sha256).hexdigest()


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
