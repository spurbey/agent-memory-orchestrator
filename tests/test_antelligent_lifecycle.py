from __future__ import annotations

import json
import os
import sys
import zipfile
from argparse import Namespace
from pathlib import Path

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.runtime.antelligent.artifacts import Artifact, download_artifact, load_manifest, select_artifact, verify_sha256
from agent_memory_orchestrator.runtime.antelligent.install import install_antelligent, installed_executable
from agent_memory_orchestrator.runtime.antelligent.launch_config import write_launch_config
from agent_memory_orchestrator.runtime.antelligent.paths import executable_name, paths_for
from agent_memory_orchestrator.runtime.antelligent.process import status_antelligent, stop_antelligent
from agent_memory_orchestrator.runtime.antelligent.startup import macos_launch_agent_plist, windows_run_command
from agent_memory_orchestrator.runtime.cli.commands import install as install_command


@pytest.fixture()
def antelligent_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    home = tmp_path / "amo-home"
    app_dir = tmp_path / "AntelligentApp"
    monkeypatch.setenv("AMO_HOME", str(home))
    monkeypatch.setenv("ANTELLIGENT_APP_DIR", str(app_dir))
    return Settings.load()


def _zip_app(path: Path, *, relpath: str | None = None) -> Path:
    relpath = relpath or executable_name()
    app_exe = path.parent / relpath
    app_exe.parent.mkdir(parents=True, exist_ok=True)
    app_exe.write_text("fake app", encoding="utf-8")
    with zipfile.ZipFile(path, "w") as archive:
        archive.write(app_exe, relpath)
    return path


def test_select_artifact_matches_platform_arch() -> None:
    manifest = {
        "version": "0.1.0",
        "artifacts": [
            {
                "platform": "windows",
                "arch": "amd64",
                "url": "https://example.invalid/antelligent.zip",
                "sha256": "abc",
                "executable_relpath": "Antelligent.exe",
            }
        ],
    }

    artifact = select_artifact(manifest, platform_name="windows", arch="amd64")

    assert artifact.version == "0.1.0"
    assert artifact.executable_relpath == "Antelligent.exe"


def test_verify_sha256_rejects_mismatch(tmp_path: Path) -> None:
    artifact = tmp_path / "app.zip"
    artifact.write_text("bad", encoding="utf-8")

    with pytest.raises(ValueError, match="SHA256 mismatch"):
        verify_sha256(artifact, "0" * 64)


def test_remote_artifact_sources_must_use_https(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="manifest URL must use HTTPS"):
        load_manifest("http://example.invalid/manifest.json")
    with pytest.raises(ValueError, match="artifact URL must use HTTPS"):
        download_artifact(
            Artifact(
                version="0.1.0",
                platform="windows",
                arch="amd64",
                url="http://example.invalid/antelligent.zip",
                sha256="",
                executable_relpath="antelligent.exe",
            ),
            tmp_path / "antelligent.zip",
        )


def test_install_from_local_artifact_writes_metadata_and_launch_config(
    antelligent_settings: Settings,
    tmp_path: Path,
) -> None:
    archive = _zip_app(tmp_path / "antelligent.zip")

    result = install_antelligent(antelligent_settings, artifact_path=archive, python_executable=sys.executable)

    exe = installed_executable(antelligent_settings)
    launch_path = paths_for(antelligent_settings).launch_config_path
    launch = json.loads(launch_path.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert exe is not None and exe.exists()
    assert Path(launch["daemon_command"]["program"]).is_absolute()
    assert launch["daemon_command"]["program"] == str(Path(sys.executable).resolve())
    assert "agent_memory_orchestrator.runtime.daemon.server" in launch["daemon_command"]["args"]
    assert "token" not in json.dumps(result).lower()


def test_write_launch_config_uses_absolute_python(antelligent_settings: Settings) -> None:
    result = write_launch_config(antelligent_settings, python_executable=sys.executable)
    config = result["config"]

    assert Path(config["daemon_command"]["program"]).is_absolute()
    assert config["daemon_command"]["program"] == str(Path(sys.executable).resolve())
    assert config["daemon_command"]["args"][:2] == ["-m", "agent_memory_orchestrator.runtime.daemon.server"]


def test_startup_helpers_are_token_free_and_login_safe(antelligent_settings: Settings, tmp_path: Path) -> None:
    exe = tmp_path / "Antelligent With Space.exe"
    exe.write_text("fake", encoding="utf-8")

    run_command = windows_run_command(exe)
    plist = macos_launch_agent_plist(antelligent_settings, exe)

    assert run_command.startswith('"') and run_command.endswith('"')
    assert len(run_command) < 260
    assert "token" not in run_command.lower()
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is False
    assert "token" not in json.dumps(plist).lower()


def test_stale_pid_is_cleaned_without_killing_anything(antelligent_settings: Settings) -> None:
    paths = paths_for(antelligent_settings)
    paths.metadata_dir.mkdir(parents=True, exist_ok=True)
    paths.pid_path.write_text(json.dumps({"pid": 99999999}) + "\n", encoding="utf-8")

    status = status_antelligent(antelligent_settings)
    stopped = stop_antelligent(antelligent_settings)

    assert status["stale_pid"] is True
    assert stopped["ok"] is True
    assert stopped["stale_pid_removed"] is True
    assert not paths.pid_path.exists()


def test_amo_install_composes_antelligent_setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setenv("AMO_HOME", str(tmp_path / "original-home"))

    def fake_install(settings: Settings, **_: object) -> dict[str, object]:
        calls.append(f"install:{settings.home}")
        return {"ok": True, "app_dir": str(tmp_path / "app")}

    def fake_startup(settings: Settings) -> dict[str, object]:
        calls.append(f"startup:{settings.home}")
        return {"ok": True}

    def fake_start(settings: Settings) -> dict[str, object]:
        calls.append(f"start:{settings.home}")
        return {"ok": True}

    monkeypatch.setattr(install_command, "install_antelligent", fake_install)
    monkeypatch.setattr(install_command, "install_startup", fake_startup)
    monkeypatch.setattr(install_command, "start_antelligent", fake_start)
    emitted: list[object] = []
    args = Namespace(
        command="install",
        target="codex",
        user_home=tmp_path / "user",
        amo_home=tmp_path / "amo",
        preset="cpu-balanced",
        embedding_model=None,
        reranker_model=None,
        qwen_model=None,
        python_command=sys.executable,
        force=True,
        dry_run=False,
        yes=True,
        json=True,
        download_models=False,
        skip_init_db=True,
        with_antelligent=True,
        antelligent_startup=True,
        antelligent_version="latest",
        antelligent_artifact=None,
    )

    status = install_command.handle_install_command(args, emit=emitted.append, emit_text=lambda _: None)

    payload = emitted[-1]
    assert status == 0
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["antelligent"]["ok"] is True
    assert calls == [f"install:{tmp_path / 'amo'}", f"startup:{tmp_path / 'amo'}", f"start:{tmp_path / 'amo'}"]
    assert os.environ["AMO_HOME"] == str(tmp_path / "original-home")


def test_amo_install_rejects_antelligent_startup_without_install(tmp_path: Path) -> None:
    emitted: list[object] = []
    args = Namespace(command="install", antelligent_startup=True, with_antelligent=False, json=True)

    status = install_command.handle_install_command(args, emit=emitted.append, emit_text=lambda _: None)

    assert status == 2
    assert emitted == [{"ok": False, "error": "--antelligent-startup requires --with-antelligent"}]
