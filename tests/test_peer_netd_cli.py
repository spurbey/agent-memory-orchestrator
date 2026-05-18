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


def test_peer_add_accepts_libp2p_identity_without_base_url(tmp_path: Path, capsys) -> None:
    assert main(["peer", "--amo-home", str(tmp_path), "init", "--node-id", "zenbook-amo"]) == 0
    capsys.readouterr()

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "add",
            "--node-id",
            "poco-amo",
            "--peer-id",
            "12D3KooWPeer",
            "--multiaddr",
            "/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer",
            "--relay-addr",
            "/ip4/relay/tcp/4001/p2p/relay/p2p-circuit/p2p/12D3KooWPeer",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["peer"]["base_url"] == ""
    assert payload["peer"]["peer_id"] == "12D3KooWPeer"
    assert payload["peer"]["multiaddrs"] == ["/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWPeer"]


def test_peer_poll_netd_fails_cleanly_when_sidecar_is_not_running(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("AMO_PEER_NETD_URL", "http://127.0.0.1:1")

    code = main(["peer", "--amo-home", str(tmp_path), "poll-netd"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert payload["ok"] is False
    assert "GET /messages failed" in payload["error"]
