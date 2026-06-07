from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import shutil
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_binary import binary_capabilities
from agent_memory_orchestrator.peer.netd_errors import PeerNetdRuntimeError
from agent_memory_orchestrator.peer.netd_platform import binary_name

DEFAULT_PEER_NETD_MANIFEST_URL = (
    "https://github.com/spurbey/agent-memory-orchestrator/releases/latest/download/peer-netd-manifest.json"
)
PEER_NETD_SIGNATURE_ALGORITHM = "ed25519"
PEER_NETD_SIGNATURE_PUBLIC_KEY_B64 = "JHcvQEQ7Y7IhikKWy8QGHYoFfbgFEyhLZIV9/cYl/04="
SUPPORTED_PEER_NETD_PLATFORMS = {"windows-amd64", "darwin-amd64", "darwin-arm64"}
REQUIRED_PEER_NETD_FLAGS = ("identity-key", "advertise-addr")
REQUIRED_PEER_NETD_PROTOCOL_CAPABILITIES = ("remote_peer_id",)


@dataclass(frozen=True, slots=True)
class PeerNetdArtifact:
    version: str
    platform: str
    arch: str
    url: str
    sha256: str
    size: int
    signature: str
    required_flags: tuple[str, ...]
    protocol_capabilities: tuple[str, ...]
    binary_name: str = ""
    minimum_amo_version: str = ""


def install_peer_netd_artifact(
    settings: Settings,
    *,
    manifest_source: str | Path | None = None,
    version: str = "latest",
    force: bool = False,
) -> dict[str, Any]:
    """Install the private peer-netd sidecar from a signed artifact manifest."""

    manifest = load_manifest(manifest_source)
    artifact = select_artifact(manifest, version=version)
    target = settings.home / ".peer" / "bin" / artifact.binary_name
    if target.exists() and not force:
        capabilities = validate_binary_capabilities(target, artifact)
        if capabilities.get("ok"):
            return {
                "ok": True,
                "installed": False,
                "reason": "already_installed",
                "binary": str(target),
                "artifact": artifact_to_dict(artifact),
                "capabilities": capabilities,
            }

    download_path = Path(tempfile.mkdtemp(prefix="amo-peer-netd-")) / artifact.binary_name
    try:
        downloaded = download_artifact(artifact, download_path)
        actual_sha256 = verify_sha256(downloaded, artifact.sha256, required=True)
        verify_size(downloaded, artifact.size)
        verify_artifact_signature(artifact)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(downloaded, target)
        if os.name != "nt":
            target.chmod(target.stat().st_mode | 0o111)
        capabilities = validate_binary_capabilities(target, artifact)
        if not capabilities.get("ok"):
            missing = capabilities.get("missing_required_flags", []) + [
                f"protocol:{item}" for item in capabilities.get("missing_protocol_capabilities", [])
            ]
            raise PeerNetdRuntimeError(
                "peer_sidecar_unavailable: installed peer-netd is incompatible"
                f" (missing: {', '.join(str(item) for item in missing)})"
            )
        metadata_path = target.parent / "amo-peer-netd.install.json"
        metadata_path.write_text(
            json.dumps(
                {
                    "version": artifact.version,
                    "platform": artifact.platform,
                    "arch": artifact.arch,
                    "sha256": actual_sha256,
                    "size": downloaded.stat().st_size,
                    "manifest_source": str(manifest_source or DEFAULT_PEER_NETD_MANIFEST_URL),
                    "installed_at": _utc_timestamp(),
                    "capabilities": capabilities,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "installed": True,
            "binary": str(target),
            "metadata": str(metadata_path),
            "artifact": artifact_to_dict(artifact),
            "capabilities": capabilities,
        }
    except Exception as exc:
        if isinstance(exc, PeerNetdRuntimeError):
            raise
        raise PeerNetdRuntimeError(f"peer_sidecar_unavailable: {exc}") from exc


def load_manifest(source: str | Path | None = None) -> dict[str, Any]:
    value = str(source or os.getenv("AMO_PEER_NETD_MANIFEST") or DEFAULT_PEER_NETD_MANIFEST_URL)
    if value.startswith("http://"):
        raise ValueError("peer-netd manifest URL must use HTTPS")
    if value.startswith("https://"):
        with urllib.request.urlopen(value, timeout=30) as response:  # noqa: S310 - explicit HTTPS release URL.
            payload = json.loads(response.read().decode("utf-8"))
    else:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("peer-netd manifest must be a JSON object")
    return payload


def select_artifact(
    manifest: dict[str, Any],
    *,
    platform_name: str | None = None,
    arch: str | None = None,
    version: str | None = None,
) -> PeerNetdArtifact:
    wanted_platform = platform_name or _platform_key()
    wanted_arch = arch or _arch_key()
    platform_dir = f"{wanted_platform}-{wanted_arch}"
    if platform_dir not in SUPPORTED_PEER_NETD_PLATFORMS:
        raise ValueError(f"unsupported peer-netd platform: {platform_dir}; supported: {', '.join(sorted(SUPPORTED_PEER_NETD_PLATFORMS))}")
    manifest_version = str(manifest.get("version") or version or "")
    for item in manifest.get("artifacts", []):
        if str(item.get("platform")) != wanted_platform or str(item.get("arch")) != wanted_arch:
            continue
        item_version = str(item.get("version") or manifest_version)
        if version and version != "latest" and item_version != version:
            continue
        artifact = _artifact_from_item(item, manifest_version=item_version, manifest=manifest)
        _validate_artifact_contract(artifact)
        return artifact
    raise ValueError(f"no peer-netd artifact for {wanted_platform}/{wanted_arch}")


def download_artifact(artifact: PeerNetdArtifact, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not artifact.url:
        raise ValueError("peer-netd artifact URL is empty")
    if artifact.url.startswith("http://"):
        raise ValueError("peer-netd artifact URL must use HTTPS")
    if artifact.url.startswith("https://"):
        with urllib.request.urlopen(artifact.url, timeout=120) as response:  # noqa: S310 - URL came from signed manifest.
            dest.write_bytes(response.read())
        return dest
    src = Path(artifact.url)
    if not src.exists():
        raise FileNotFoundError(src)
    shutil.copy2(src, dest)
    return dest


def verify_sha256(path: Path, expected: str, *, required: bool = False) -> str:
    actual = sha256_file(path)
    if required and not expected:
        raise ValueError("peer-netd artifact sha256 is required")
    if expected and actual.lower() != expected.lower():
        raise ValueError(f"peer-netd artifact sha256 mismatch: expected {expected}, got {actual}")
    return actual


def verify_size(path: Path, expected_size: int) -> int:
    actual = path.stat().st_size
    if expected_size <= 0:
        raise ValueError("peer-netd artifact size is required")
    if actual != expected_size:
        raise ValueError(f"peer-netd artifact size mismatch: expected {expected_size}, got {actual}")
    return actual


def verify_artifact_signature(artifact: PeerNetdArtifact) -> None:
    if not artifact.signature:
        raise ValueError("peer-netd artifact Ed25519 signature is required")
    verify_ed25519_signature(
        public_key_b64=PEER_NETD_SIGNATURE_PUBLIC_KEY_B64,
        signature_b64=artifact.signature,
        message=signature_payload(artifact),
    )


def verify_ed25519_signature(*, public_key_b64: str, signature_b64: str, message: bytes) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - exercised only in minimal dependency environments.
        raise ValueError("cryptography is required to verify peer-netd Ed25519 signatures") from exc
    public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64))
    public_key.verify(base64.b64decode(signature_b64), message)


def signature_payload(artifact: PeerNetdArtifact) -> bytes:
    payload = artifact_to_dict(artifact)
    payload.pop("signature", None)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def validate_binary_capabilities(binary: Path, artifact: PeerNetdArtifact) -> dict[str, Any]:
    required_flags = tuple(dict.fromkeys([*REQUIRED_PEER_NETD_FLAGS, *artifact.required_flags]))
    required_protocol = tuple(
        dict.fromkeys([*REQUIRED_PEER_NETD_PROTOCOL_CAPABILITIES, *artifact.protocol_capabilities])
    )
    return binary_capabilities(
        binary,
        required_flags=required_flags,
        required_protocol_capabilities=required_protocol,
    )


def artifact_to_dict(artifact: PeerNetdArtifact) -> dict[str, Any]:
    return {
        "version": artifact.version,
        "platform": artifact.platform,
        "arch": artifact.arch,
        "url": artifact.url,
        "sha256": artifact.sha256,
        "size": artifact.size,
        "signature": artifact.signature,
        "required_flags": list(artifact.required_flags),
        "protocol_capabilities": list(artifact.protocol_capabilities),
        "binary_name": artifact.binary_name,
        "minimum_amo_version": artifact.minimum_amo_version,
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _artifact_from_item(item: dict[str, Any], *, manifest_version: str, manifest: dict[str, Any]) -> PeerNetdArtifact:
    artifact_platform = str(item.get("platform") or "")
    return PeerNetdArtifact(
        version=str(item.get("version") or manifest_version),
        platform=artifact_platform,
        arch=str(item.get("arch") or ""),
        url=str(item.get("url") or ""),
        sha256=str(item.get("sha256") or ""),
        size=int(item.get("size") or 0),
        signature=str(_signature_value(item.get("signature"))),
        required_flags=tuple(str(value).strip().lstrip("-") for value in item.get("required_flags", []) if str(value).strip()),
        protocol_capabilities=tuple(
            str(value).strip() for value in item.get("protocol_capabilities", []) if str(value).strip()
        ),
        binary_name=str(item.get("binary_name") or _artifact_binary_name(artifact_platform)),
        minimum_amo_version=str(item.get("minimum_amo_version") or manifest.get("minimum_amo_version") or ""),
    )


def _artifact_binary_name(platform_name: str) -> str:
    if platform_name == "windows":
        return "amo-peer-netd.exe"
    if platform_name in {"darwin", "linux"}:
        return "amo-peer-netd"
    return binary_name()


def _signature_value(value: Any) -> str:
    if isinstance(value, dict):
        algorithm = str(value.get("algorithm") or "").lower()
        if algorithm and algorithm != PEER_NETD_SIGNATURE_ALGORITHM:
            raise ValueError(f"unsupported peer-netd signature algorithm: {algorithm}")
        return str(value.get("value") or "")
    return str(value or "")


def _validate_artifact_contract(artifact: PeerNetdArtifact) -> None:
    if not artifact.version:
        raise ValueError("peer-netd artifact version is required")
    if not artifact.url:
        raise ValueError("peer-netd artifact url is required")
    if _is_remote_url(artifact.url) and not artifact.url.startswith("https://"):
        raise ValueError("peer-netd remote artifact URL must use HTTPS")
    if not artifact.sha256:
        raise ValueError("peer-netd artifact sha256 is required")
    if artifact.size <= 0:
        raise ValueError("peer-netd artifact size is required")
    if not artifact.signature:
        raise ValueError("peer-netd artifact Ed25519 signature is required")
    missing_flags = [item for item in REQUIRED_PEER_NETD_FLAGS if item not in artifact.required_flags]
    missing_protocol = [
        item for item in REQUIRED_PEER_NETD_PROTOCOL_CAPABILITIES if item not in artifact.protocol_capabilities
    ]
    if missing_flags:
        raise ValueError(f"peer-netd artifact manifest missing required flags: {', '.join(missing_flags)}")
    if missing_protocol:
        raise ValueError(
            "peer-netd artifact manifest missing protocol capabilities: " + ", ".join(missing_protocol)
        )


def _platform_key() -> str:
    system = platform.system().lower()
    if system.startswith("windows"):
        return "windows"
    if system == "darwin":
        return "darwin"
    return system or "unknown"


def _arch_key() -> str:
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        return "amd64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    return machine or "unknown"


def _is_remote_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https"}


def _utc_timestamp() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DEFAULT_PEER_NETD_MANIFEST_URL",
    "PeerNetdArtifact",
    "SUPPORTED_PEER_NETD_PLATFORMS",
    "artifact_to_dict",
    "download_artifact",
    "install_peer_netd_artifact",
    "load_manifest",
    "select_artifact",
    "sha256_file",
    "signature_payload",
    "validate_binary_capabilities",
    "verify_artifact_signature",
    "verify_ed25519_signature",
    "verify_sha256",
    "verify_size",
]
