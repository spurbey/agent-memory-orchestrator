from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.peer.netd_runtime import PeerNetdLaunchOptions, PeerNetdRuntime, PeerNetdRuntimeError


def test_peer_netd_runtime_builds_expected_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMO_PEER_SECRET", "test-secret")
    runtime = PeerNetdRuntime(make_settings(tmp_path))

    args = runtime.args_for(
        Path("amo-peer-netd"),
        PeerNetdLaunchOptions(
            node_id="zenbook-amo",
            listen_addr="/ip4/0.0.0.0/tcp/9000",
            api_addr="127.0.0.1:8799",
            shared_secret_env="AMO_PEER_SECRET",
            require_signature=True,
            bootstrap_addrs=("/ip4/127.0.0.1/tcp/9100/p2p/bootstrap",),
            static_relays=("/ip4/127.0.0.1/tcp/9200/p2p/relay",),
            mdns=True,
            rendezvous_server=True,
            relay_service=True,
            nat_service=True,
            auto_relay=True,
            hole_punching=True,
            force_private=True,
            advertise_localhost_dns=True,
        ),
    )

    assert args[:7] == [
        "amo-peer-netd",
        "--node-id",
        "zenbook-amo",
        "--listen",
        "/ip4/0.0.0.0/tcp/9000",
        "--api",
        "127.0.0.1:8799",
    ]
    assert ["--shared-secret", "test-secret"] == args[args.index("--shared-secret") : args.index("--shared-secret") + 2]
    for flag in [
        "--require-signature",
        "--mdns",
        "--rendezvous-server",
        "--relay-service",
        "--nat-service",
        "--auto-relay",
        "--hole-punching",
        "--force-private",
        "--advertise-localhost-dns",
    ]:
        assert flag in args
    assert args[args.index("--bootstrap") + 1] == "/ip4/127.0.0.1/tcp/9100/p2p/bootstrap"
    assert args[args.index("--static-relay") + 1] == "/ip4/127.0.0.1/tcp/9200/p2p/relay"


def test_peer_netd_runtime_requires_configured_secret(tmp_path: Path) -> None:
    runtime = PeerNetdRuntime(make_settings(tmp_path))

    with pytest.raises(PeerNetdRuntimeError, match="shared secret env var is not set"):
        runtime.args_for(Path("amo-peer-netd"), PeerNetdLaunchOptions(shared_secret_env="AMO_MISSING_SECRET"))


def test_peer_netd_runtime_reports_missing_state_as_stopped(tmp_path: Path) -> None:
    runtime = PeerNetdRuntime(make_settings(tmp_path))

    status = runtime.status()

    assert status["ok"] is True
    assert status["running"] is False
    assert status["api_ok"] is False
    assert status["pid"] is None


def test_peer_netd_runtime_reads_and_clears_state(tmp_path: Path) -> None:
    runtime = PeerNetdRuntime(make_settings(tmp_path))
    state = {
        "pid": 12345,
        "api_url": "http://127.0.0.1:8788",
        "binary": str(tmp_path / "amo-peer-netd.exe"),
    }

    runtime.write_state(state)

    assert json.loads(runtime.state_path.read_text(encoding="utf-8"))["pid"] == 12345
    assert runtime.read_state()["api_url"] == "http://127.0.0.1:8788"
    runtime.clear_state()
    assert runtime.read_state() == {}


def test_peer_netd_runtime_refuses_managed_dynamic_api_port(tmp_path: Path) -> None:
    runtime = PeerNetdRuntime(make_settings(tmp_path))

    with pytest.raises(PeerNetdRuntimeError, match="fixed --api"):
        runtime.start(PeerNetdLaunchOptions(api_addr="127.0.0.1:0"), build_if_missing=False)


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
