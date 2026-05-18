from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.app.cli import main


def test_peer_netd_status_uses_amo_home(tmp_path: Path, capsys) -> None:
    code = main(["peer", "--amo-home", str(tmp_path), "netd", "status"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["running"] is False
    assert payload["state_path"].startswith(str(tmp_path))


def test_peer_enable_rejects_dynamic_api_port_without_building(tmp_path: Path, capsys) -> None:
    code = main(["peer", "--amo-home", str(tmp_path), "enable", "--api", "127.0.0.1:0", "--no-build"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert payload["ok"] is False
    assert "fixed --api" in payload["error"]
