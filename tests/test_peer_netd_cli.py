from __future__ import annotations

import json
from pathlib import Path

from agent_memory_orchestrator.app.cli import main
from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer import PeerService
from agent_memory_orchestrator.peer.invites import build_peer_invite
from agent_memory_orchestrator.peer.netd_runtime import PeerNetdRuntime, binary_name
from agent_memory_orchestrator.peer.netd_runtime import PeerNetdLaunchOptions
from agent_memory_orchestrator.peer.netd_service import PeerNetdServiceOptions
from agent_memory_orchestrator.peer.netd_service import install_service as install_netd_service
from agent_memory_orchestrator.peer import netd_runtime as netd_runtime_module
from agent_memory_orchestrator.peer import netd_service as netd_service_module


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
    assert any(check["name"] == "netd_binary" and check["status"] == "warn" for check in payload["checks"])
    assert any(check["name"] == "netd_runtime" and check["status"] == "warn" for check in payload["checks"])


def test_peer_netd_rebuilds_stale_binary_when_go_is_available(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path))
    runtime = PeerNetdRuntime(Settings.load())
    binary_path = tmp_path / ".peer" / "bin" / binary_name()
    binary_path.parent.mkdir(parents=True)
    binary_path.write_text("stale", encoding="utf-8")
    capability_calls = 0

    def fake_capabilities(self: PeerNetdRuntime, binary: Path | None = None) -> dict:
        nonlocal capability_calls
        capability_calls += 1
        if capability_calls == 1:
            return {"ok": False, "missing_required_flags": ["identity-key"], "binary": str(binary)}
        return {"ok": True, "missing_required_flags": [], "binary": str(binary)}

    def fake_build(self: PeerNetdRuntime, output_path: Path | None = None) -> dict:
        target = output_path or self.default_binary_path()
        target.write_text("fresh", encoding="utf-8")
        return {"ok": True, "binary": str(target)}

    monkeypatch.setattr(PeerNetdRuntime, "binary_capabilities", fake_capabilities)
    monkeypatch.setattr(PeerNetdRuntime, "build", fake_build)

    assert runtime.prepare_binary(build_if_missing=True) == binary_path
    assert binary_path.read_text(encoding="utf-8") == "fresh"


def test_peer_netd_restarts_when_running_launch_config_differs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path))
    runtime = PeerNetdRuntime(Settings.load())
    relay_addr = "/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"
    old_options = PeerNetdLaunchOptions(node_id="node-a")
    new_options = PeerNetdLaunchOptions(
        node_id="node-a",
        static_relays=(relay_addr,),
        auto_relay=True,
        hole_punching=True,
        rendezvous_addr=relay_addr,
        rendezvous_namespace="amo-team",
    )
    written_state: dict = {}
    stopped = []

    class FakeProcess:
        pid = 4567

    monkeypatch.setattr(
        PeerNetdRuntime,
        "status",
        lambda self: {"ok": True, "running": True, "api_ok": True, "api_url": "http://127.0.0.1:8788"},
    )
    monkeypatch.setattr(
        PeerNetdRuntime,
        "read_state",
        lambda self: {"pid": 1234, "launch_config": runtime.launch_config(old_options)},
    )
    monkeypatch.setattr(PeerNetdRuntime, "stop", lambda self: stopped.append(True) or {"ok": True, "stopped": True})
    monkeypatch.setattr(PeerNetdRuntime, "prepare_binary", lambda self, build_if_missing=True: tmp_path / binary_name())
    monkeypatch.setattr(PeerNetdRuntime, "args_for", lambda self, binary, options: [str(binary)])
    monkeypatch.setattr(PeerNetdRuntime, "wait_for_health", lambda self, api_addr: {"ok": True, "relay_addrs": []})
    monkeypatch.setattr(PeerNetdRuntime, "post_start", lambda self, options, api_url: {})
    monkeypatch.setattr(PeerNetdRuntime, "write_state", lambda self, state: written_state.update(state))
    monkeypatch.setattr(netd_runtime_module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())

    result = runtime.start(new_options, build_if_missing=False)

    assert result["ok"] is True
    assert result["already_running"] is False
    assert result["restart"] == {"ok": True, "stopped": True}
    assert stopped == [True]
    assert written_state["launch_config"]["static_relays"] == [relay_addr]
    assert written_state["launch_config"]["rendezvous_namespace"] == "amo-team"


def test_peer_netd_reuses_running_sidecar_when_launch_config_matches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path))
    runtime = PeerNetdRuntime(Settings.load())
    options = PeerNetdLaunchOptions(node_id="node-a")
    stopped = []

    monkeypatch.setattr(
        PeerNetdRuntime,
        "status",
        lambda self: {"ok": True, "running": True, "api_ok": True, "api_url": "http://127.0.0.1:8788"},
    )
    monkeypatch.setattr(
        PeerNetdRuntime,
        "read_state",
        lambda self: {"pid": 1234, "launch_config": runtime.launch_config(options)},
    )
    monkeypatch.setattr(PeerNetdRuntime, "stop", lambda self: stopped.append(True) or {"ok": True})
    monkeypatch.setattr(PeerNetdRuntime, "post_start", lambda self, options, api_url: {})

    result = runtime.start(options, build_if_missing=False)

    assert result["ok"] is True
    assert result["already_running"] is True
    assert result["launch_config_match"] is True
    assert stopped == []


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


def test_peer_relay_profile_save_list_and_show(tmp_path: Path, capsys) -> None:
    relay_addr = "/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"

    save_code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "relay",
            "save",
            "--name",
            "amo-test",
            "--addr",
            relay_addr,
            "--namespace",
            "amo-team",
        ]
    )
    save_payload = json.loads(capsys.readouterr().out)

    assert save_code == 0
    assert save_payload["profile"]["name"] == "amo-test"
    assert save_payload["profile"]["relay_addr"] == relay_addr
    assert save_payload["profile"]["rendezvous_namespace"] == "amo-team"

    list_code = main(["peer", "--amo-home", str(tmp_path), "relay", "list"])
    list_payload = json.loads(capsys.readouterr().out)
    assert list_code == 0
    assert [item["name"] for item in list_payload["profiles"]] == ["amo-test"]

    show_code = main(["peer", "--amo-home", str(tmp_path), "relay", "show", "--name", "amo-test"])
    show_payload = json.loads(capsys.readouterr().out)
    assert show_code == 0
    assert show_payload["profile"]["relay_addr"] == relay_addr


def test_peer_enable_expands_saved_relay_profile(tmp_path: Path, capsys, monkeypatch) -> None:
    relay_addr = "/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"
    assert (
        main(
            [
                "peer",
                "--amo-home",
                str(tmp_path),
                "relay",
                "save",
                "--name",
                "amo-test",
                "--addr",
                relay_addr,
                "--namespace",
                "amo-team",
            ]
        )
        == 0
    )
    capsys.readouterr()
    captured_options = []

    def fake_start(self: PeerNetdRuntime, options, *, build_if_missing: bool = True) -> dict:
        captured_options.append((options, build_if_missing))
        return {"ok": True, "api_url": "http://127.0.0.1:8788"}

    monkeypatch.setattr(PeerNetdRuntime, "start", fake_start)

    code = main(["peer", "--amo-home", str(tmp_path), "enable", "--node-id", "node-a", "--relay", "amo-test", "--no-build"])
    payload = json.loads(capsys.readouterr().out)
    options, build_if_missing = captured_options[0]

    assert code == 0
    assert payload["ok"] is True
    assert build_if_missing is False
    assert options.static_relays == (relay_addr,)
    assert options.auto_relay is True
    assert options.hole_punching is True
    assert options.rendezvous_addr == relay_addr
    assert options.rendezvous_namespace == "amo-team"


def test_peer_create_invite_expands_saved_relay_profile(tmp_path: Path, capsys, monkeypatch) -> None:
    relay_addr = "/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"
    circuit_addr = relay_addr + "/p2p-circuit/p2p/12D3KooWNodeA"
    assert main(["peer", "--amo-home", str(tmp_path), "init", "--node-id", "node-a"]) == 0
    capsys.readouterr()
    assert (
        main(
            [
                "peer",
                "--amo-home",
                str(tmp_path),
                "relay",
                "save",
                "--name",
                "amo-test",
                "--addr",
                relay_addr,
                "--namespace",
                "amo-team",
            ]
        )
        == 0
    )
    capsys.readouterr()

    def fake_status(self: PeerNetdRuntime) -> dict:
        return {
            "ok": True,
            "api_ok": True,
            "health": {
                "peer_id": "12D3KooWNodeA",
                "listen_addrs": ["/ip4/127.0.0.1/tcp/9001/p2p/12D3KooWNodeA"],
                "relay_addrs": [circuit_addr],
            },
        }

    monkeypatch.setattr(PeerNetdRuntime, "status", fake_status)

    code = main(["peer", "--amo-home", str(tmp_path), "create-invite", "--relay", "amo-test", "--auto-approve"])
    payload = json.loads(capsys.readouterr().out)

    assert code == 0
    assert payload["invite"]["card"]["relay_addrs"] == [circuit_addr]
    assert payload["invite"]["card"]["rendezvous_addr"] == relay_addr
    assert payload["invite"]["card"]["rendezvous_namespace"] == "amo-team"


def test_peer_setup_can_save_relay_from_invite_and_accept(tmp_path: Path, capsys, monkeypatch) -> None:
    relay_addr = "/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"
    invite = build_peer_invite(
        card={
            "amo_peer_card_version": 1,
            "node_id": "host",
            "display_name": "Host",
            "capabilities": ["graph_retrieval"],
            "transport": "libp2p",
            "base_url": "",
            "peer_id": "12D3KooWHost",
            "multiaddrs": [relay_addr + "/p2p-circuit/p2p/12D3KooWHost"],
            "relay_addrs": [relay_addr + "/p2p-circuit/p2p/12D3KooWHost"],
            "rendezvous_addr": relay_addr,
            "rendezvous_namespace": "amo-team",
        },
        trust="trusted",
        auto_approve=True,
    )
    invite_path = tmp_path / "host.invite.json"
    invite_path.write_text(json.dumps(invite, indent=2), encoding="utf-8")
    captured_options = []

    def fake_start(self: PeerNetdRuntime, options, *, build_if_missing: bool = True) -> dict:
        captured_options.append(options)
        return {"ok": True, "api_url": "http://127.0.0.1:8788"}

    def fake_accept(self: PeerService, invite_payload: dict, **kwargs) -> dict:
        return {"ok": True, "imported_peer": invite_payload["card"]["node_id"]}

    monkeypatch.setattr(PeerNetdRuntime, "start", fake_start)
    monkeypatch.setattr(PeerService, "accept_peer_invite", fake_accept)

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "setup",
            "--node-id",
            "friend",
            "--display-name",
            "Friend",
            "--invite",
            str(invite_path),
            "--no-build",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    options = captured_options[0]

    assert code == 0
    assert payload["relay_profile"]["name"] == "amo-team"
    assert payload["relay_profile"]["relay_addr"] == relay_addr
    assert payload["accept_invite"]["ok"] is True
    assert options.static_relays == (relay_addr,)
    assert options.rendezvous_addr == relay_addr
    assert options.rendezvous_namespace == "amo-team"


def test_peer_setup_returns_nonzero_when_startup_install_fails(tmp_path: Path, capsys, monkeypatch) -> None:
    from agent_memory_orchestrator.app import cli as cli_module

    def fake_start(self: PeerNetdRuntime, options, *, build_if_missing: bool = True) -> dict:
        return {"ok": True, "api_url": "http://127.0.0.1:8788"}

    monkeypatch.setattr(PeerNetdRuntime, "start", fake_start)
    monkeypatch.setattr(cli_module, "install_peer_netd_service", lambda *args, **kwargs: {"ok": False, "error": "boom"})

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "setup",
            "--node-id",
            "node-a",
            "--install-startup",
            "--no-build",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert code == 1
    assert payload["ok"] is False
    assert payload["startup"]["error"] == "boom"


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


def test_peer_create_and_accept_invite_code(tmp_path: Path, capsys) -> None:
    home_a = tmp_path / "node-a"
    home_b = tmp_path / "node-b"
    invite_path = tmp_path / "node-a.invite.json"
    assert main(["peer", "--amo-home", str(home_a), "init", "--node-id", "node-a", "--display-name", "Node A"]) == 0
    capsys.readouterr()

    create_code = main(
        [
            "peer",
            "--amo-home",
            str(home_a),
            "create-invite",
            "--base-url",
            "http://127.0.0.1:8787",
            "--label",
            "Node A invite",
            "--auto-approve",
            "--expires-minutes",
            "60",
            "--out",
            str(invite_path),
        ]
    )
    create_payload = json.loads(capsys.readouterr().out)
    assert create_code == 0
    assert invite_path.exists()
    assert create_payload["invite"]["card"]["node_id"] == "node-a"
    assert create_payload["invite"]["auto_approve"] is True
    assert create_payload["invite"]["invite_token"]
    assert create_payload["invite_code"].startswith("amo-peer-invite:")

    assert main(["peer", "--amo-home", str(home_b), "init", "--node-id", "node-b"]) == 0
    capsys.readouterr()
    accept_code = main(
        [
            "peer",
            "--amo-home",
            str(home_b),
            "accept-invite",
            "--code",
            create_payload["invite_code"],
        ]
    )
    accept_payload = json.loads(capsys.readouterr().out)

    assert accept_code == 0
    assert accept_payload["imported_peer"]["node_id"] == "node-a"
    assert accept_payload["imported_peer"]["trust"] == "trusted"
    assert accept_payload["response_card"] is None
    assert "no usable peer address" in accept_payload["response_card_error"]


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
    assert "peer-agent" in payload["watcher"]["watch_command"]
    assert "watch" in payload["watcher"]["watch_command"]


def test_peer_netd_windows_watcher_task_runs_immediately_after_install(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMO_HOME", str(tmp_path))
    monkeypatch.setattr(netd_service_module, "_is_windows", lambda: True)
    options = PeerNetdServiceOptions(apply=True, with_watcher=True)
    commands = []

    def fake_run_commands(payload: list[list[str]]) -> dict:
        commands.extend(payload)
        return {"ok": True, "results": [{"ok": True, "command": item} for item in payload]}

    monkeypatch.setattr(netd_service_module, "_run_commands", fake_run_commands)

    result = install_netd_service(Settings.load(), PeerNetdLaunchOptions(node_id="node-a"), options)

    assert result["ok"] is True
    assert result["watcher"]["start_command"] in commands
    assert result["watcher"]["start_command"][:2] == ["schtasks", "/Run"]


def test_peer_relay_start_uses_public_helper_defaults(tmp_path: Path, capsys, monkeypatch) -> None:
    captured_options = []

    def fake_start(self: PeerNetdRuntime, options, *, build_if_missing: bool = True) -> dict:
        captured_options.append((options, build_if_missing))
        return {
            "ok": True,
            "health": {
                "listen_addrs": ["/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"],
            },
        }

    monkeypatch.setattr(PeerNetdRuntime, "start", fake_start)

    code = main(
        [
            "peer",
            "--amo-home",
            str(tmp_path),
            "relay",
            "start",
            "--node-id",
            "amo-relay-prod",
            "--advertise-addr",
            "/ip4/203.0.113.10/tcp/4001",
            "--namespace",
            "amo-team",
            "--no-build",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    options, build_if_missing = captured_options[0]
    assert code == 0
    assert build_if_missing is False
    assert options.node_id == "amo-relay-prod"
    assert options.relay_service is True
    assert options.rendezvous_server is True
    assert options.nat_service is True
    assert options.force_public is True
    assert options.advertise_addrs == ("/ip4/203.0.113.10/tcp/4001",)
    assert payload["relay"]["relay_multiaddr"] == "/ip4/203.0.113.10/tcp/4001/p2p/12D3KooWRelay"
    assert payload["relay"]["rendezvous_namespace"] == "amo-team"
