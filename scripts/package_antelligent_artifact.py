from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

APP_BUNDLE_NAME = "Antelligent.app"
CRATE_BINARY = "antelligent"


def main() -> int:
    parser = argparse.ArgumentParser(description="Package Antelligent portable release artifacts.")
    parser.add_argument("--platform", required=True, choices=["windows", "darwin"])
    parser.add_argument("--arch", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--version", required=True)
    parser.add_argument("--minimum-amo-version", required=True)
    parser.add_argument("--target", help="Rust target triple used for the build.")
    parser.add_argument("--target-root", type=Path, default=Path("apps/antelligent/src-tauri/target"))
    parser.add_argument("--output-dir", type=Path, default=Path("dist/antelligent"))
    parser.add_argument("--base-url", required=True, help="Release asset URL base ending at the tag path.")
    args = parser.parse_args()

    payload = package_artifact(
        platform_name=args.platform,
        arch=args.arch,
        version=args.version,
        minimum_amo_version=args.minimum_amo_version,
        target=args.target,
        target_root=args.target_root,
        output_dir=args.output_dir,
        base_url=args.base_url.rstrip("/"),
    )
    print(json.dumps(payload, indent=2))
    return 0


def package_artifact(
    *,
    platform_name: str,
    arch: str,
    version: str,
    minimum_amo_version: str,
    target: str | None,
    target_root: Path,
    output_dir: Path,
    base_url: str,
) -> dict[str, Any]:
    _require_https_base_url(base_url)
    output_dir.mkdir(parents=True, exist_ok=True)
    if platform_name == "windows":
        source = _windows_executable(target_root, target)
        artifact_name = f"antelligent-windows-{arch}.zip"
        artifact_path = output_dir / artifact_name
        executable_relpath = source.name
        _zip_file(source, artifact_path, executable_relpath)
    elif platform_name == "darwin":
        source = _macos_app_bundle(target_root, target)
        artifact_name = f"antelligent-darwin-{arch}.tar.gz"
        artifact_path = output_dir / artifact_name
        executable_relpath = f"{APP_BUNDLE_NAME}/Contents/MacOS/{CRATE_BINARY}"
        _tar_dir(source, artifact_path, APP_BUNDLE_NAME)
    else:  # pragma: no cover - argparse constrains this.
        raise ValueError(f"unsupported platform: {platform_name}")

    digest = sha256_file(artifact_path)
    fragment = {
        "version": version,
        "minimum_amo_version": minimum_amo_version,
        "artifacts": [
            {
                "platform": platform_name,
                "arch": arch,
                "url": f"{base_url}/{artifact_name}",
                "sha256": digest,
                "executable_relpath": executable_relpath,
                "minimum_amo_version": minimum_amo_version,
            }
        ],
    }
    fragment_path = output_dir / f"antelligent-{platform_name}-{arch}.manifest-fragment.json"
    fragment_path.write_text(json.dumps(fragment, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "artifact": str(artifact_path),
        "manifest_fragment": str(fragment_path),
        "sha256": digest,
        "executable_relpath": executable_relpath,
    }


def _release_dir(target_root: Path, target: str | None) -> Path:
    if target:
        candidate = target_root / target / "release"
        if candidate.exists():
            return candidate
    return target_root / "release"


def _windows_executable(target_root: Path, target: str | None) -> Path:
    release = _release_dir(target_root, target)
    candidates = [release / "antelligent.exe", release / "Antelligent.exe"]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise FileNotFoundError(f"Antelligent Windows executable not found under {release}")


def _macos_app_bundle(target_root: Path, target: str | None) -> Path:
    release = _release_dir(target_root, target)
    candidates = [
        release / "bundle" / "macos" / APP_BUNDLE_NAME,
        target_root / "universal-apple-darwin" / "release" / "bundle" / "macos" / APP_BUNDLE_NAME,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    matches = sorted(target_root.glob(f"**/release/bundle/macos/{APP_BUNDLE_NAME}"))
    if matches:
        return matches[-1]
    raise FileNotFoundError(f"Antelligent macOS app bundle not found under {target_root}")


def _zip_file(source: Path, dest: Path, arcname: str) -> None:
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(source, arcname)


def _tar_dir(source: Path, dest: Path, arcname: str) -> None:
    with tarfile.open(dest, "w:gz", dereference=True) as archive:
        archive.add(source, arcname=arcname, recursive=True)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_https_base_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Antelligent release base_url must use HTTPS")


if __name__ == "__main__":
    raise SystemExit(main())
