from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any


def _netd_envelope_id(envelope: dict[str, Any]) -> str:
    signature = str(envelope.get("signature") or "").strip()
    if signature:
        return signature
    canonical = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _with_transport_auth_metadata(payload: dict[str, Any], auth: dict[str, Any]) -> dict[str, Any]:
    message_type = str(payload.get("type") or payload.get("message_type") or "").strip()
    if message_type not in {"context_request", "context_response"}:
        return payload
    updated = dict(payload)
    metadata = updated.get("metadata") if isinstance(updated.get("metadata"), dict) else {}
    metadata = dict(metadata)
    metadata["transport_auth"] = {
        "auth": str(auth.get("auth") or ""),
        "authenticated": bool(auth.get("authenticated")),
        "remote_peer_id": str(auth.get("remote_peer_id") or "").strip(),
    }
    updated["metadata"] = metadata
    return updated


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
