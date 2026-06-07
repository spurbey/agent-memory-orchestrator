from __future__ import annotations

import os
import sys
from argparse import Namespace
from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.runtime.cli.commands import install as install_command


def test_amo_install_with_peer_installs_sidecar_without_peer_enrollment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[Path] = []

    def fake_install(settings: Settings, **_: object) -> dict[str, object]:
        calls.append(settings.home)
        return {"ok": True, "binary": str(settings.home / ".peer" / "bin" / "amo-peer-netd.exe")}

    monkeypatch.setenv("AMO_HOME", str(tmp_path / "original-home"))
    monkeypatch.setattr(install_command, "install_peer_netd_artifact", fake_install)
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
        with_peer=True,
        peer_netd_manifest="",
        peer_netd_version="latest",
        with_antelligent=False,
        antelligent_startup=False,
        antelligent_version="latest",
        antelligent_artifact=None,
    )

    status = install_command.handle_install_command(args, emit=emitted.append, emit_text=lambda _: None)

    payload = emitted[-1]
    assert status == 0
    assert isinstance(payload, dict)
    assert payload["ok"] is True
    assert payload["peer"]["ok"] is True
    assert calls == [tmp_path / "amo"]
    assert not (tmp_path / "amo" / ".peer" / "peers.json").exists()
    assert os.environ["AMO_HOME"] == str(tmp_path / "original-home")


def test_amo_install_with_peer_returns_nonzero_when_sidecar_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(install_command, "install_peer_netd_artifact", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
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
        with_peer=True,
        peer_netd_manifest="",
        peer_netd_version="latest",
        with_antelligent=False,
        antelligent_startup=False,
        antelligent_version="latest",
        antelligent_artifact=None,
    )

    status = install_command.handle_install_command(args, emit=emitted.append, emit_text=lambda _: None)

    payload = emitted[-1]
    assert status == 1
    assert payload["peer"]["ok"] is False
    assert "boom" in payload["peer"]["error"]
