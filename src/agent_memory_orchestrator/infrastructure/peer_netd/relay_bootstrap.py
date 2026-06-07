from __future__ import annotations

import base64
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_RELAY_BOOTSTRAP_URL = (
    "https://github.com/spurbey/agent-memory-orchestrator/releases/latest/download/peer-relay-bootstrap.json"
)
RELAY_BOOTSTRAP_SIGNATURE_PUBLIC_KEY_B64 = "JHcvQEQ7Y7IhikKWy8QGHYoFfbgFEyhLZIV9/cYl/04="


@dataclass(frozen=True, slots=True)
class ManagedRelayProfile:
    name: str
    relay_addr: str
    rendezvous_addr: str
    rendezvous_namespace: str
    auto_relay: bool = True
    hole_punching: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "relay_addr": self.relay_addr,
            "rendezvous_addr": self.rendezvous_addr,
            "rendezvous_namespace": self.rendezvous_namespace,
            "auto_relay": self.auto_relay,
            "hole_punching": self.hole_punching,
        }


def load_managed_relay_profile(source: str | Path | None = None) -> ManagedRelayProfile:
    metadata = load_relay_bootstrap(source)
    verify_relay_bootstrap_signature(metadata)
    profiles = metadata.get("relay_profiles", [])
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("managed relay bootstrap has no relay_profiles")
    item = profiles[0]
    if not isinstance(item, dict):
        raise ValueError("managed relay profile must be an object")
    return _profile_from_item(item)


def load_relay_bootstrap(source: str | Path | None = None) -> dict[str, Any]:
    value = str(source or os.getenv("AMO_PEER_RELAY_BOOTSTRAP") or DEFAULT_RELAY_BOOTSTRAP_URL)
    if value.startswith("http://"):
        raise ValueError("managed relay bootstrap URL must use HTTPS")
    if value.startswith("https://"):
        with urllib.request.urlopen(value, timeout=30) as response:  # noqa: S310 - explicit HTTPS release URL.
            payload = json.loads(response.read().decode("utf-8"))
    else:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("managed relay bootstrap must be a JSON object")
    return payload


def verify_relay_bootstrap_signature(metadata: dict[str, Any]) -> None:
    signature = metadata.get("signature")
    if not isinstance(signature, dict):
        raise ValueError("managed relay bootstrap signature is required")
    algorithm = str(signature.get("algorithm") or "").lower()
    if algorithm != "ed25519":
        raise ValueError(f"unsupported managed relay signature algorithm: {algorithm}")
    value = str(signature.get("value") or "")
    if not value:
        raise ValueError("managed relay bootstrap signature value is required")
    verify_ed25519_signature(
        public_key_b64=RELAY_BOOTSTRAP_SIGNATURE_PUBLIC_KEY_B64,
        signature_b64=value,
        message=relay_bootstrap_signature_payload(metadata),
    )


def relay_bootstrap_signature_payload(metadata: dict[str, Any]) -> bytes:
    payload = dict(metadata)
    payload.pop("signature", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_ed25519_signature(*, public_key_b64: str, signature_b64: str, message: bytes) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - dependency is installed in normal AMO envs.
        raise ValueError("cryptography is required to verify managed relay Ed25519 signatures") from exc
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    public_key.verify(base64.b64decode(signature_b64), message)


def _profile_from_item(item: dict[str, Any]) -> ManagedRelayProfile:
    name = str(item.get("name") or "amo-managed").strip()
    relay_addr = str(item.get("relay_addr") or "").strip()
    rendezvous_addr = str(item.get("rendezvous_addr") or relay_addr).strip()
    namespace = str(item.get("rendezvous_namespace") or "").strip()
    if not relay_addr:
        raise ValueError("managed relay profile relay_addr is required")
    if not rendezvous_addr:
        raise ValueError("managed relay profile rendezvous_addr is required")
    if not namespace:
        raise ValueError("managed relay profile rendezvous_namespace is required")
    if _is_remote_url(relay_addr):
        raise ValueError("managed relay profile relay_addr must be a libp2p multiaddr, not a URL")
    return ManagedRelayProfile(
        name=name,
        relay_addr=relay_addr,
        rendezvous_addr=rendezvous_addr,
        rendezvous_namespace=namespace,
        auto_relay=bool(item.get("auto_relay", True)),
        hole_punching=bool(item.get("hole_punching", True)),
    )


def _is_remote_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


__all__ = [
    "DEFAULT_RELAY_BOOTSTRAP_URL",
    "ManagedRelayProfile",
    "load_managed_relay_profile",
    "load_relay_bootstrap",
    "relay_bootstrap_signature_payload",
    "verify_ed25519_signature",
    "verify_relay_bootstrap_signature",
]
