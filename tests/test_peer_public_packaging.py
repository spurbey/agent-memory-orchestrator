from __future__ import annotations

import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path


def test_public_package_excludes_peer_netd_go_source_and_infra() -> None:
    root = Path(__file__).resolve().parents[1]
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    data_files = pyproject.get("tool", {}).get("setuptools", {}).get("data-files", {})
    package_data = pyproject.get("tool", {}).get("setuptools", {}).get("package-data", {})
    manifest = (root / "MANIFEST.in").read_text(encoding="utf-8")

    assert "peer-netd" not in data_files
    assert "bin/*/amo-peer-netd" not in package_data.get("agent_memory_orchestrator", [])
    assert "bin/*/amo-peer-netd.exe" not in package_data.get("agent_memory_orchestrator", [])
    assert "prune peer-netd" in manifest
    assert "prune infra" in manifest
    assert "prune src/agent_memory_orchestrator/bin" in manifest


def test_peer_sidecar_distribution_is_windows_macos_only() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "src" / "agent_memory_orchestrator" / "infrastructure" / "peer_netd" / "artifacts.py").read_text(
        encoding="utf-8"
    )

    assert "windows-amd64" in text
    assert "darwin-amd64" in text
    assert "darwin-arm64" in text
    assert "linux-amd64" not in text
    assert sys.platform


def test_built_public_archives_exclude_private_peer_runtime() -> None:
    root = Path(__file__).resolve().parents[1]
    dist_dir = root / "dist"
    archives = [*dist_dir.glob("agent_memory_orchestrator-*.whl"), *dist_dir.glob("agent_memory_orchestrator-*.tar.gz")]
    if not archives:
        return

    forbidden_fragments = (
        "peer-netd/",
        "infra/",
        "host.invite.json",
        "friend-invite-code.txt",
        "friends.txt",
        ".evidence/",
        ".graph/",
        ".data/",
        ".tmp/",
        ".sqlite",
        ".db",
        ".zip",
        "agent_memory_orchestrator/bin/",
        "amo-peer-netd.exe",
        "amo-peer-netd",
    )
    for archive in archives:
        names = _archive_names(archive)
        hits = [
            name
            for name in names
            if any(fragment in name.replace("\\", "/") for fragment in forbidden_fragments)
        ]
        assert hits == [], f"{archive.name} contains private/runtime peer artifacts: {hits[:20]}"


def _archive_names(path: Path) -> list[str]:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as handle:
            return handle.namelist()
    with tarfile.open(path, "r:gz") as handle:
        return handle.getnames()
