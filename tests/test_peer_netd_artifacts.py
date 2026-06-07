from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from agent_memory_orchestrator.core.config import Settings
from agent_memory_orchestrator.infrastructure.peer_netd import artifacts
from agent_memory_orchestrator.infrastructure.peer_netd.artifacts import install_peer_netd_artifact
from agent_memory_orchestrator.infrastructure.peer_netd.artifacts import select_artifact
from agent_memory_orchestrator.infrastructure.peer_netd.artifacts import verify_sha256
from agent_memory_orchestrator.infrastructure.peer_netd.relay_bootstrap import load_managed_relay_profile


def test_peer_netd_artifact_contract_requires_windows_or_macos() -> None:
    manifest = {"version": "0.1.0", "artifacts": []}

    with pytest.raises(ValueError, match="unsupported peer-netd platform"):
        select_artifact(manifest, platform_name="linux", arch="amd64")


def test_peer_netd_artifact_manifest_requires_security_fields() -> None:
    manifest = {
        "version": "0.1.0",
        "artifacts": [
            {
                "platform": "windows",
                "arch": "amd64",
                "url": "https://example.invalid/amo-peer-netd.exe",
                "sha256": "abc",
                "size": 12,
                "required_flags": ["identity-key", "advertise-addr"],
                "protocol_capabilities": ["remote_peer_id"],
            }
        ],
    }

    with pytest.raises(ValueError, match="signature"):
        select_artifact(manifest, platform_name="windows", arch="amd64")


def test_peer_netd_artifact_hash_mismatch_fails(tmp_path: Path) -> None:
    artifact = tmp_path / "amo-peer-netd.exe"
    artifact.write_bytes(b"payload")

    with pytest.raises(ValueError, match="sha256 mismatch"):
        verify_sha256(artifact, "0" * 64, required=True)


def test_peer_netd_artifact_installs_verified_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binary = tmp_path / "downloaded.exe"
    binary.write_bytes(b"fake sidecar")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "artifacts": [
                    {
                        "platform": "windows",
                        "arch": "amd64",
                        "url": str(binary),
                        "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                        "size": binary.stat().st_size,
                        "signature": {"algorithm": "ed25519", "value": "sig"},
                        "required_flags": ["identity-key", "advertise-addr"],
                        "protocol_capabilities": ["remote_peer_id"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(artifacts, "_platform_key", lambda: "windows")
    monkeypatch.setattr(artifacts, "_arch_key", lambda: "amd64")
    monkeypatch.setattr(artifacts, "verify_ed25519_signature", lambda **_: None)
    monkeypatch.setattr(
        artifacts,
        "binary_capabilities",
        lambda *_args, **_kwargs: {
            "ok": True,
            "missing_required_flags": [],
            "missing_protocol_capabilities": [],
        },
    )
    settings = make_settings(tmp_path / "home")

    result = install_peer_netd_artifact(settings, manifest_source=manifest_path)

    installed = settings.home / ".peer" / "bin" / "amo-peer-netd.exe"
    assert result["ok"] is True
    assert result["installed"] is True
    assert installed.read_bytes() == b"fake sidecar"
    assert (installed.parent / "amo-peer-netd.install.json").exists()


def test_managed_relay_bootstrap_loads_signed_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bootstrap = tmp_path / "relay.json"
    bootstrap.write_text(
        json.dumps(
            {
                "version": "0.1.0",
                "relay_profiles": [
                    {
                        "name": "amo-managed",
                        "relay_addr": "/dns4/relay.example.com/tcp/4001/p2p/12D3KooWRelay",
                        "rendezvous_addr": "/dns4/relay.example.com/tcp/4001/p2p/12D3KooWRelay",
                        "rendezvous_namespace": "amo-prod",
                    }
                ],
                "signature": {"algorithm": "ed25519", "value": "sig"},
            }
        ),
        encoding="utf-8",
    )
    from agent_memory_orchestrator.infrastructure.peer_netd import relay_bootstrap

    monkeypatch.setattr(relay_bootstrap, "verify_ed25519_signature", lambda **_: None)

    profile = load_managed_relay_profile(bootstrap)

    assert profile.name == "amo-managed"
    assert profile.rendezvous_namespace == "amo-prod"


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
