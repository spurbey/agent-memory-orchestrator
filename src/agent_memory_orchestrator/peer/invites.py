from __future__ import annotations

import base64
import hashlib
import json
import zlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent_memory_orchestrator.peer.cards import peer_from_card

PEER_INVITE_VERSION = 1
INVITE_CODE_PREFIX = "amo-peer-invite:"
VALID_TRUST = {"trusted", "limited", "blocked"}


def build_peer_invite(
    *,
    card: dict[str, Any],
    trust: str = "trusted",
    shared_secret_env: str = "",
    label: str = "",
) -> dict[str, Any]:
    """Build a portable invite bundle around a peer card.

    The bundle contains public reachability details only. It deliberately stores
    the shared-secret environment variable name, not the secret value.
    """
    _validate_trust(trust)
    node_id = str(card.get("node_id") or "").strip()
    if not node_id:
        raise ValueError("invite card node_id is required")
    # Reuse card validation so unusable transport addresses fail before sharing.
    peer_from_card(card, trust=trust, shared_secret_env=shared_secret_env)
    return {
        "amo_peer_invite_version": PEER_INVITE_VERSION,
        "invite_id": f"invite_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "created_by_node_id": node_id,
        "label": label.strip(),
        "recommended_trust": trust,
        "shared_secret_env": shared_secret_env.strip(),
        "card_sha256": peer_card_sha256(card),
        "card": card,
    }


def parse_peer_invite(invite: dict[str, Any]) -> dict[str, Any]:
    if int(invite.get("amo_peer_invite_version") or 0) != PEER_INVITE_VERSION:
        raise ValueError("unsupported AMO peer invite version")
    card = invite.get("card")
    if not isinstance(card, dict):
        raise ValueError("peer invite card must be an object")
    expected_hash = str(invite.get("card_sha256") or "").strip()
    actual_hash = peer_card_sha256(card)
    if expected_hash and expected_hash != actual_hash:
        raise ValueError("peer invite card hash mismatch")
    trust = str(invite.get("recommended_trust") or "trusted").strip() or "trusted"
    _validate_trust(trust)
    shared_secret_env = str(invite.get("shared_secret_env") or "").strip()
    peer_from_card(card, trust=trust, shared_secret_env=shared_secret_env)
    return {"card": card, "trust": trust, "shared_secret_env": shared_secret_env, "card_sha256": actual_hash}


def encode_invite_code(invite: dict[str, Any]) -> str:
    payload = json.dumps(invite, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    compressed = zlib.compress(payload, level=9)
    return INVITE_CODE_PREFIX + base64.urlsafe_b64encode(compressed).decode("ascii").rstrip("=")


def decode_invite_code(code: str) -> dict[str, Any]:
    value = code.strip()
    if value.startswith(INVITE_CODE_PREFIX):
        value = value[len(INVITE_CODE_PREFIX) :]
    padding = "=" * (-len(value) % 4)
    try:
        compressed = base64.urlsafe_b64decode((value + padding).encode("ascii"))
        payload = zlib.decompress(compressed)
        parsed = json.loads(payload.decode("utf-8"))
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        raise ValueError("invalid AMO peer invite code") from exc
    if not isinstance(parsed, dict):
        raise ValueError("AMO peer invite code must decode to an object")
    return parsed


def peer_card_sha256(card: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(card).encode("utf-8")).hexdigest()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _validate_trust(trust: str) -> None:
    if trust not in VALID_TRUST:
        raise ValueError(f"trust must be one of: {', '.join(sorted(VALID_TRUST))}")
