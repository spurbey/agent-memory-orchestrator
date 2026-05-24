from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_runtime import PeerNetdLaunchOptions
from agent_memory_orchestrator.peer import netd_service as netd_service_module
from agent_memory_orchestrator.peer.netd_service import PeerNetdServiceOptions, install_service, install_service_plan


def test_peer_netd_service_plan_contains_enable_command(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    plan = install_service_plan(
        settings,
        PeerNetdLaunchOptions(
            node_id="zenbook-amo",
            api_addr="127.0.0.1:8799",
            identity_key_path=str(tmp_path / "identity.key"),
            shared_secret_env="AMO_PEER_SECRET",
            require_signature=True,
            mdns=True,
        ),
        PeerNetdServiceOptions(service_name="AMO Test Peer", apply=False),
    )

    command = plan["enable_command"]
    assert plan["ok"] is True
    assert plan["apply"] is False
    assert "agent_memory_orchestrator.app.cli" in command
    assert "--amo-home" in command
    assert str(tmp_path) in command
    assert "--node-id" in command
    assert "zenbook-amo" in command
    assert "--identity-key" in command
    assert str(tmp_path / "identity.key") in command
    assert "--shared-secret-env" in command
    assert "AMO_PEER_SECRET" in command
    assert "--require-signature" in command
    assert "--mdns" in command


def test_peer_netd_service_plan_can_include_watcher(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    plan = install_service_plan(
        settings,
        PeerNetdLaunchOptions(node_id="zenbook-amo"),
        PeerNetdServiceOptions(service_name="AMO Test Peer", with_watcher=True),
    )

    watcher = plan["watcher"]
    watch_command = watcher["watch_command"]
    assert watcher["service_name"]
    assert "agent_memory_orchestrator.app.cli" in watch_command
    assert "--amo-home" in watch_command
    assert str(tmp_path) in watch_command
    assert "peer-agent" in watch_command
    assert "watch" in watch_command
    assert watcher["install_command"]
    assert watcher["uninstall_command"]


def test_peer_netd_service_windows_plan_uses_short_wrapper_scripts(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    relay_addr = "/ip4/13.126.104.170/tcp/4001/p2p/12D3KooWRelay"
    monkeypatch.setattr(netd_service_module, "_is_windows", lambda: True)

    plan = install_service_plan(
        settings,
        PeerNetdLaunchOptions(
            node_id="zenbook-amo",
            static_relays=(relay_addr,),
            auto_relay=True,
            hole_punching=True,
            rendezvous_addr=relay_addr,
            rendezvous_namespace="amo-test",
        ),
        PeerNetdServiceOptions(service_name="AMO Test Peer", with_watcher=True),
    )

    assert plan["platform"] == "windows"
    assert plan["script_path"].endswith("amo-test-peer.cmd")
    assert relay_addr in plan["script"]
    assert relay_addr not in plan["install_command"][plan["install_command"].index("/TR") + 1]
    assert plan["watcher"]["script_path"].endswith("amo-test-peer-watcher.cmd")
    assert "peer-agent" in plan["watcher"]["script"]
    assert "/RL" in plan["install_command"]


def test_peer_netd_service_windows_apply_falls_back_to_startup_folder(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    appdata = tmp_path / "AppData" / "Roaming"
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setattr(netd_service_module, "_is_windows", lambda: True)
    monkeypatch.setattr(netd_service_module, "_is_macos", lambda: False)

    def fake_run_commands(payload: list[list[str]], *, ignore_failures: bool = False) -> dict:
        return {"ok": False, "results": [{"ok": False, "command": item, "stderr": "Access is denied."} for item in payload]}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(netd_service_module, "_run_commands", fake_run_commands)
    monkeypatch.setattr(netd_service_module.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    result = install_service(
        settings,
        PeerNetdLaunchOptions(node_id="win-amo"),
        PeerNetdServiceOptions(service_name="AMO Test Peer", apply=True, with_watcher=True),
    )

    startup_dir = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    assert result["ok"] is True
    assert result["startup_method"] == "startup-folder"
    assert (startup_dir / "amo-test-peer.vbs").exists()
    assert (startup_dir / "amo-test-peer-watcher.vbs").exists()


def test_peer_netd_service_plan_supports_macos_launch_agents(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(netd_service_module, "_is_windows", lambda: False)
    monkeypatch.setattr(netd_service_module, "_is_macos", lambda: True)
    monkeypatch.setattr(netd_service_module, "_launchd_domain", lambda: "gui/501")

    plan = install_service_plan(
        settings,
        PeerNetdLaunchOptions(node_id="macbook-amo"),
        PeerNetdServiceOptions(service_name="AMO Test Peer", with_watcher=True),
    )

    assert plan["platform"] == "launchd-user"
    assert plan["service_name"] == "com.agent-memory-orchestrator.amo-test-peer"
    assert plan["install_command"][:3] == ["launchctl", "bootstrap", "gui/501"]
    assert plan["start_command"][:3] == ["launchctl", "kickstart", "-k"]
    assert "ProgramArguments" in plan["plist"]
    assert "peer-agent" in plan["watcher"]["watch_command"]
    assert plan["watcher"]["install_command"][:3] == ["launchctl", "bootstrap", "gui/501"]


def test_peer_netd_service_macos_apply_refreshes_loaded_agents(tmp_path: Path, monkeypatch) -> None:
    settings = make_settings(tmp_path)
    monkeypatch.setattr(netd_service_module, "_is_windows", lambda: False)
    monkeypatch.setattr(netd_service_module, "_is_macos", lambda: True)
    monkeypatch.setattr(netd_service_module, "_launchd_domain", lambda: "gui/501")
    commands: list[list[str]] = []

    def fake_run_commands(payload: list[list[str]], *, ignore_failures: bool = False) -> dict:
        commands.extend(payload)
        return {"ok": True, "results": [{"ok": True, "command": item, "ignore_failures": ignore_failures} for item in payload]}

    monkeypatch.setattr(netd_service_module, "_run_commands", fake_run_commands)

    result = install_service(
        settings,
        PeerNetdLaunchOptions(node_id="macbook-amo"),
        PeerNetdServiceOptions(service_name="AMO Test Peer", apply=True, with_watcher=True),
    )

    assert result["ok"] is True
    assert (tmp_path / ".peer" / "netd" / "logs").is_dir()
    assert any(command[:3] == ["launchctl", "bootout", "gui/501"] for command in commands)
    assert any(command[:3] == ["launchctl", "bootstrap", "gui/501"] for command in commands)
    assert any(command[:3] == ["launchctl", "kickstart", "-k"] for command in commands)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / "memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=64,
        embedding_model="hash-fallback",
        reranker_model="BAAI/bge-reranker-base",
        vector_backend="disabled",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=tmp_path / "graph" / "amo.kuzu",
        retrieval_db_path=tmp_path / "retrieval.sqlite",
        evidence_dir=tmp_path / ".evidence",
    )
