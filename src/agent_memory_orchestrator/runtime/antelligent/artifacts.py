from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tarfile import TarFile
from tarfile import open as tar_open
from typing import Any

from .paths import arch_key, platform_key

DEFAULT_MANIFEST_URL = "https://github.com/spurbey/agent-memory-orchestrator/releases/latest/download/antelligent-manifest.json"


@dataclass(frozen=True, slots=True)
class Artifact:
    version: str
    platform: str
    arch: str
    url: str
    sha256: str
    executable_relpath: str
    minimum_amo_version: str = ""


def load_manifest(source: str | Path | None = None) -> dict[str, Any]:
    value = str(source or DEFAULT_MANIFEST_URL)
    if value.startswith("http://"):
        raise ValueError("Antelligent manifest URL must use HTTPS")
    if value.startswith("https://"):
        with urllib.request.urlopen(value, timeout=30) as response:  # noqa: S310 - fixed HTTPS/default or explicit user URL.
            return json.loads(response.read().decode("utf-8"))
    return json.loads(Path(value).read_text(encoding="utf-8"))


def select_artifact(manifest: dict[str, Any], *, platform_name: str | None = None, arch: str | None = None, version: str | None = None) -> Artifact:
    wanted_platform = platform_name or platform_key()
    wanted_arch = arch or arch_key()
    manifest_version = str(manifest.get("version") or version or "")
    for item in manifest.get("artifacts", []):
        if str(item.get("platform")) != wanted_platform or str(item.get("arch")) != wanted_arch:
            continue
        item_version = str(item.get("version") or manifest_version)
        if version and version != "latest" and item_version != version:
            continue
        return Artifact(
            version=item_version,
            platform=wanted_platform,
            arch=wanted_arch,
            url=str(item.get("url") or ""),
            sha256=str(item.get("sha256") or ""),
            executable_relpath=str(item.get("executable_relpath") or ""),
            minimum_amo_version=str(item.get("minimum_amo_version") or manifest.get("minimum_amo_version") or ""),
        )
    raise ValueError(f"no Antelligent artifact for {wanted_platform}/{wanted_arch}")


def download_artifact(artifact: Artifact, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not artifact.url:
        raise ValueError("artifact URL is empty")
    if artifact.url.startswith("http://"):
        raise ValueError("Antelligent artifact URL must use HTTPS")
    if artifact.url.startswith("https://"):
        with urllib.request.urlopen(artifact.url, timeout=120) as response:  # noqa: S310 - release artifact URL from manifest.
            dest.write_bytes(response.read())
        return dest
    src = Path(artifact.url)
    if not src.exists():
        raise FileNotFoundError(src)
    shutil.copy2(src, dest)
    return dest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> str:
    actual = sha256_file(path)
    if expected and actual.lower() != expected.lower():
        raise ValueError(f"Antelligent artifact SHA256 mismatch: expected {expected}, got {actual}")
    return actual


def extract_artifact(archive_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    suffixes = "".join(archive_path.suffixes).lower()
    if suffixes.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as archive:
            _safe_extract_zip(archive, dest)
        return
    if suffixes.endswith(".tar.gz") or suffixes.endswith(".tgz"):
        with tar_open(archive_path, "r:gz") as archive:
            _safe_extract_tar(archive, dest)
        return
    raise ValueError(f"unsupported Antelligent artifact format: {archive_path.name}")


def _safe_extract_zip(archive: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for member in archive.infolist():
        target = (dest / member.filename).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"unsafe zip member path: {member.filename}")
    archive.extractall(dest)


def _safe_extract_tar(archive: TarFile, dest: Path) -> None:
    root = dest.resolve()
    members = archive.getmembers()
    for member in members:
        target = (dest / member.name).resolve()
        if root != target and root not in target.parents:
            raise ValueError(f"unsafe tar member path: {member.name}")
        if member.islnk() or member.issym():
            raise ValueError(f"refusing symlink in Antelligent artifact: {member.name}")
    archive.extractall(dest, members=members)


def temp_dir(prefix: str = "antelligent-") -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


__all__ = [
    "Artifact",
    "DEFAULT_MANIFEST_URL",
    "download_artifact",
    "extract_artifact",
    "load_manifest",
    "select_artifact",
    "sha256_file",
    "temp_dir",
    "verify_sha256",
]
