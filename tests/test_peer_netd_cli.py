from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.app.cli import main
from agent_memory_orchestrator.peer import PeerService
from agent_memory_orchestrator.peer.netd_runtime import binary_name


def test_peer_netd_status_uses_amo_home(tmp_path: Path, capsys) -> None:
    code = main(["peer", "--amo-home", str(tmp_path), "netd", "status"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["running"] is False
    assert payload["state_path"].startswith(str(tmp_path))


def test_peer_doctor_reports_missing_identity_without_failing_command(tmp_path: Path, capsys) -> None:
    code = main(["peer", "--amo-home", str(tmp_path), "doctor"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["ready"] is False
    assert any(check["name"] == "peer_identity" and check["status"] == "fail" for check in payload["checks"])


def test_peer_doctor_strict_returns_nonzero_until_runtime_ready(tmp_path: Path, capsys) -> None:
    code = main(["peer", "--amo-home", str(tmp_path), "doctor", "--strict"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 1
    assert payload["ready"] is False


def test_peer_doctor_accepts_initialized_node_with_existing_binary(tmp_path: Path, capsys) -> None:
    assert main(["peer", "--amo-home", str(tmp_path), "init", "--node-id", "node-a"]) == 0
    capsys.readouterr()
    binary_path = tmp_path / ".peer" / "bin" / binary_name()
    binary_path.parent.mkdir(parents=True)
    binary_path.write_text("fake", encoding="utf-8")

    code = main(["peer", "--amo-home", str(tmp_path), "doctor"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["blocking_count"] == 0
    assert any(check["name"] == "netd_binary" and check["status"] == "pass" for check in payload["checks"])
    assert any(check["name"] == "netd_runtime" and check["status"] == "warn" for check in payload["checks"])


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


def test_peer_share_and_import_card_with_base_url(tmp_path: Path, capsys) -> None:
    home_a = tmp_path / "node-a"
    home_b = tmp_path / "node-b"
    card_path = tmp_path / "node-a.card.json"
    assert main(["peer", "--amo-home", str(home_a), "init", "--node-id", "node-a", "--display-name", "Node A"]) == 0
    capsys.readouterr()

    share_code = main(
        [
            "peer",
            "--amo-home",
            str(home_a),
            "share-card",
            "--base-url",
            "http://127.0.0.1:8787",
            "--out",
            str(card_path),
        ]
    )
    share_payload = json.loads(capsys.readouterr().out)
    assert share_code == 0
    assert card_path.exists()
    assert share_payload["card"]["node_id"] == "node-a"

    assert main(["peer", "--amo-home", str(home_b), "init", "--node-id", "node-b"]) == 0
    capsys.readouterr()
    import_code = main(["peer", "--amo-home", str(home_b), "import-card", "--file", str(card_path)])
    import_payload = json.loads(capsys.readouterr().out)

    assert import_code == 0
    assert import_payload["peer"]["node_id"] == "node-a"
    assert import_payload["peer"]["base_url"] == "http://127.0.0.1:8787"


def test_peer_poll_netd_fails_cleanly_when_sidecar_is_not_running(tmp_path: Path, capsys, monkeypatch) -> None:
    monkeypatch.setenv("AMO_PEER_NETD_URL", "http://127.0.0.1:1")

    code = main(["peer", "--amo-home", str(tmp_path), "poll-netd"])

    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 1
    assert payload["ok"] is False
    assert "GET /messages failed" in payload["error"]


def test_peer_poll_netd_watch_streams_json_lines(tmp_path: Path, capsys, monkeypatch) -> None:
    calls: list[int | None] = []

    def fake_process_netd_inbox(self: PeerService, limit: int | None = None) -> dict:
        calls.append(limit)
        return {"ok": True, "count": len(calls)}

    monkeypatch.setattr(PeerService, "process_netd_inbox", fake_process_netd_inbox)

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "poll-netd",
            "--watch",
            "--interval-seconds",
            "0.01",
            "--max-iterations",
            "2",
            "--limit",
            "5",
        ]
    )

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    assert calls == [5, 5]
    assert lines == [{"ok": True, "count": 1}, {"ok": True, "count": 2}]


def test_peer_poll_netd_watch_continues_after_transient_error(tmp_path: Path, capsys, monkeypatch) -> None:
    calls = 0

    def fake_process_netd_inbox(self: PeerService, limit: int | None = None) -> dict:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("sidecar not ready")
        return {"ok": True, "count": 0}

    monkeypatch.setattr(PeerService, "process_netd_inbox", fake_process_netd_inbox)

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "poll-netd",
            "--watch",
            "--interval-seconds",
            "0.01",
            "--max-iterations",
            "2",
        ]
    )

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 0
    assert lines[0]["ok"] is False
    assert lines[0]["watching"] is True
    assert lines[1] == {"ok": True, "count": 0}


def test_peer_poll_netd_watch_fail_fast_returns_nonzero(tmp_path: Path, capsys, monkeypatch) -> None:
    def fake_process_netd_inbox(self: PeerService, limit: int | None = None) -> dict:
        raise RuntimeError("sidecar not ready")

    monkeypatch.setattr(PeerService, "process_netd_inbox", fake_process_netd_inbox)

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "poll-netd",
            "--watch",
            "--interval-seconds",
            "0.01",
            "--max-iterations",
            "2",
            "--fail-fast",
        ]
    )

    captured = capsys.readouterr()
    lines = [json.loads(line) for line in captured.out.splitlines() if line.strip()]
    assert code == 1
    assert lines == [{"ok": False, "error": "sidecar not ready", "watching": False}]


def test_peer_netd_install_service_is_plan_by_default(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "netd",
            "install-service",
            "--node-id",
            "zenbook-amo",
            "--api",
            "127.0.0.1:8799",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["apply"] is False
    assert "enable" in payload["enable_command"]


def test_peer_netd_install_service_can_plan_watcher(tmp_path: Path, capsys) -> None:
    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "netd",
            "install-service",
            "--node-id",
            "zenbook-amo",
            "--with-watch",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["ok"] is True
    assert payload["apply"] is False
    assert "enable" in payload["enable_command"]
    assert "poll-netd" in payload["watcher"]["watch_command"]
    assert "--watch" in payload["watcher"]["watch_command"]
