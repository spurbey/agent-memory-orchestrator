from __future__ import annotations

import json
import zipfile
from pathlib import Path

from scripts.package_antelligent_artifact import package_artifact


def test_package_antelligent_windows_artifact_manifest(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    exe = target_root / "release" / "antelligent.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"fake antelligent exe")
    output = tmp_path / "out"

    result = package_artifact(
        platform_name="windows",
        arch="amd64",
        version="0.1.0",
        minimum_amo_version="0.1.4",
        target=None,
        target_root=target_root,
        output_dir=output,
        base_url="https://github.com/spurbey/agent-memory-orchestrator/releases/download/antelligent-v0.1.0",
    )

    artifact = Path(result["artifact"])
    fragment = json.loads(Path(result["manifest_fragment"]).read_text(encoding="utf-8"))
    with zipfile.ZipFile(artifact) as archive:
        assert archive.namelist() == ["antelligent.exe"]
    item = fragment["artifacts"][0]
    assert item["platform"] == "windows"
    assert item["arch"] == "amd64"
    assert item["url"].endswith("/antelligent-windows-amd64.zip")
    assert item["sha256"] == result["sha256"]
    assert item["executable_relpath"] == "antelligent.exe"
