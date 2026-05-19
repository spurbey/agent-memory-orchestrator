from __future__ import annotations

from pathlib import Path

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_runtime import PeerNetdLaunchOptions
from agent_memory_orchestrator.peer.netd_service import PeerNetdServiceOptions, install_service_plan


def test_peer_netd_service_plan_contains_enable_command(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)

    plan = install_service_plan(
        settings,
        PeerNetdLaunchOptions(
            node_id="zenbook-amo",
            api_addr="127.0.0.1:8799",
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
    assert "poll-netd" in watch_command
    assert "--watch" in watch_command
    assert watcher["install_command"]
    assert watcher["uninstall_command"]


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
