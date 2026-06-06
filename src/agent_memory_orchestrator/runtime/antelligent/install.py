from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from ...core.config import Settings
from .artifacts import Artifact, download_artifact, extract_artifact, load_manifest, select_artifact, temp_dir, verify_sha256
from .launch_config import write_launch_config
from .paths import executable_name, executable_names, is_macos, paths_for


def install_antelligent(
    settings: Settings,
    *,
    version: str = "latest",
    artifact_path: Path | None = None,
    manifest: str | Path | None = None,
    force: bool = False,
    python_executable: str | None = None,
) -> dict[str, Any]:
    paths = paths_for(settings)
    paths.metadata_dir.mkdir(parents=True, exist_ok=True)
    launch = write_launch_config(settings, python_executable=python_executable)

    if artifact_path is None:
        manifest_payload = load_manifest(manifest)
        artifact = select_artifact(manifest_payload, version=version)
        archive_path = temp_dir("antelligent-download-") / Path(artifact.url).name
        download_artifact(artifact, archive_path)
    else:
        archive_path = Path(artifact_path).resolve()
        artifact = Artifact(
            version=version or "local",
            platform="local",
            arch="local",
            url=str(archive_path),
            sha256="",
            executable_relpath="",
        )
    actual_sha = verify_sha256(archive_path, artifact.sha256, required=artifact_path is None)

    paths.app_dir.parent.mkdir(parents=True, exist_ok=True)
    extract_dir = Path(tempfile.mkdtemp(prefix="antelligent-extract-", dir=str(paths.app_dir.parent)))
    extract_artifact(archive_path, extract_dir)
    executable_relpath = artifact.executable_relpath or _find_executable_relpath(extract_dir)
    executable = extract_dir / executable_relpath
    if not executable.exists():
        raise FileNotFoundError(f"Antelligent executable not found in artifact: {executable_relpath}")

    previous_dir = paths.app_dir.with_name(paths.app_dir.name + ".previous")
    if paths.app_dir.exists() and not force and not _looks_like_antelligent_install(paths.app_dir):
        _remove_path(extract_dir)
        raise FileExistsError(f"refusing to replace non-Antelligent directory: {paths.app_dir}")
    try:
        if paths.app_dir.exists():
            if previous_dir.exists():
                shutil.rmtree(previous_dir)
            paths.app_dir.replace(previous_dir)
        shutil.move(str(extract_dir), str(paths.app_dir))
        installed_executable = paths.app_dir / executable_relpath
        if not installed_executable.exists():
            raise FileNotFoundError(f"installed Antelligent executable missing: {installed_executable}")
        if previous_dir.exists():
            shutil.rmtree(previous_dir)
    except Exception:
        _remove_path(paths.app_dir)
        if previous_dir.exists():
            previous_dir.replace(paths.app_dir)
        _remove_path(extract_dir)
        raise

    metadata = {
        "ok": True,
        "version": artifact.version,
        "platform": artifact.platform,
        "arch": artifact.arch,
        "app_dir": str(paths.app_dir),
        "executable": str(installed_executable),
        "executable_relpath": executable_relpath,
        "artifact_sha256": actual_sha,
        "launch_config": launch.get("path"),
    }
    paths.install_json.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    _cleanup_temp(archive_path.parent, artifact_path is None)
    return metadata


def uninstall_antelligent(settings: Settings, *, remove_config: bool = False) -> dict[str, Any]:
    paths = paths_for(settings)
    removed: list[str] = []
    for path in [paths.app_dir, paths.install_json, paths.pid_path]:
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(str(path))
        elif path.exists():
            path.unlink()
            removed.append(str(path))
    if remove_config:
        for path in [paths.launch_config_path, paths.token_path]:
            if path.exists():
                path.unlink()
                removed.append(str(path))
    return {"ok": True, "removed": removed, "app_dir": str(paths.app_dir)}


def install_metadata(settings: Settings) -> dict[str, Any] | None:
    path = paths_for(settings).install_json
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def installed_executable(settings: Settings) -> Path | None:
    metadata = install_metadata(settings)
    if metadata and metadata.get("executable"):
        return Path(str(metadata["executable"]))
    paths = paths_for(settings)
    candidates = [paths.app_dir / name for name in executable_names()]
    if is_macos():
        for name in executable_names():
            candidates.append(paths.app_dir / "Antelligent.app" / "Contents" / "MacOS" / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _find_executable_relpath(root: Path) -> str:
    names = list(executable_names())
    if is_macos():
        names.append("Antelligent.app/Contents/MacOS/Antelligent")
    for name in names:
        candidate = root / name
        if candidate.exists():
            return name.replace("/", os.sep)
    matches = list(root.rglob(executable_name()))
    if matches:
        return str(matches[0].relative_to(root))
    raise FileNotFoundError("could not infer Antelligent executable from artifact")


def _looks_like_antelligent_install(path: Path) -> bool:
    if not path.exists():
        return True
    return any((path / name).exists() for name in [executable_name(), "Antelligent.app"])


def _cleanup_temp(path: Path, should_remove: bool) -> None:
    if should_remove and path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        path.unlink()


__all__ = ["install_antelligent", "install_metadata", "installed_executable", "uninstall_antelligent"]
