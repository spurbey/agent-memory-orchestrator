from __future__ import annotations

import pytest

from agent_memory_orchestrator.app import daemon as daemon_module
from agent_memory_orchestrator.app.daemon import (
    DaemonAlreadyRunning,
    _DaemonOwnerLock,
    _bounded_int,
    _graph_workbench_html,
    _load_web_asset,
    _read_graph_service,
    _session_cockpit_html,
    _v2_stage_requires_graph_write_lock,
    _web_asset_bytes,
)
from agent_memory_orchestrator.core.config import Settings


def test_root_daemon_module_keeps_compatibility_exports() -> None:
    from agent_memory_orchestrator import daemon as compat_daemon

    assert compat_daemon._bounded_int("5", default=1, minimum=1, maximum=10) == 5


def test_bounded_int_clamps_invalid_and_extreme_values() -> None:
    assert _bounded_int(None, default=25, minimum=1, maximum=100) == 25
    assert _bounded_int("abc", default=25, minimum=1, maximum=100) == 25
    assert _bounded_int("-50", default=25, minimum=1, maximum=100) == 1
    assert _bounded_int("5000", default=25, minimum=1, maximum=100) == 100
    assert _bounded_int("42", default=25, minimum=1, maximum=100) == 42


def test_web_assets_load_from_package_static_folder() -> None:
    html = _load_web_asset("index.html")
    js, content_type = _web_asset_bytes("amo.js")
    control_room_js, control_room_content_type = _web_asset_bytes("js/control-room/app.js")
    graph_html = _load_web_asset("graph.html")
    graph_js, graph_content_type = _web_asset_bytes("js/graph/workbench.js")
    graph_css, graph_css_type = _web_asset_bytes("css/graph-workbench.css")

    assert "AMO Control Room" in html
    assert "Production pipeline" in _session_cockpit_html()
    assert 'type="module" src="/web/amo.js"' in _session_cockpit_html()
    assert b"control-room/app.js" in js
    assert content_type.startswith("application/javascript")
    assert b"function setView" in control_room_js
    assert control_room_content_type.startswith("application/javascript")
    assert "3D Memory Workbench" in graph_html
    assert "3D Memory Workbench" in _graph_workbench_html()
    assert b"async function loadGraph" in graph_js
    assert graph_content_type.startswith("application/javascript")
    assert b".graph-workbench" in graph_css
    assert graph_css_type.startswith("text/css")


def test_web_asset_loader_blocks_path_traversal() -> None:
    with pytest.raises(ValueError):
        _web_asset_bytes("../daemon.py")


def test_daemon_v2_lock_scope_keeps_long_stages_unlocked() -> None:
    assert _v2_stage_requires_graph_write_lock("kuzu_write") is True
    assert _v2_stage_requires_graph_write_lock("central_version_merge") is True
    assert _v2_stage_requires_graph_write_lock("qwen_reasoning") is False
    assert _v2_stage_requires_graph_write_lock("ast_code_nodes") is False
    assert _v2_stage_requires_graph_write_lock("embeddings") is False


def test_daemon_owner_lock_blocks_second_process_owner(tmp_path) -> None:
    settings = _settings_for_daemon_lock(tmp_path)
    first = _DaemonOwnerLock.acquire(settings)
    try:
        with pytest.raises(DaemonAlreadyRunning):
            _DaemonOwnerLock.acquire(settings)
    finally:
        first.release()

    second = _DaemonOwnerLock.acquire(settings)
    second.release()


def test_read_graph_service_uses_repo_central_graph_read_only(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings_for_daemon_lock(tmp_path)
    opened_stores: list[tuple[object, bool]] = []
    opened_services: list[tuple[object, object | None, bool]] = []

    class FakeStore:
        def __init__(self, graph_path: object, *, read_only: bool = False) -> None:
            self.graph_path = graph_path
            self.read_only = read_only
            opened_stores.append((graph_path, read_only))

    class FakeGraph:
        def __init__(self, graph_settings: Settings, *, store: object | None = None, read_only: bool = False, **_: object) -> None:
            opened_services.append((graph_settings.graph_path, store, read_only))

        def close(self) -> None:
            return None

    monkeypatch.setattr(daemon_module, "KuzuGraphStore", FakeStore)
    monkeypatch.setattr(daemon_module, "GraphRagService", FakeGraph)

    graph = _read_graph_service(settings, repo_id="repo:amo")

    expected_path = daemon_module.repo_central_graph_path(settings, "repo:amo")
    assert graph is not None
    assert opened_stores == [(expected_path, True)]
    service_path, service_store, service_read_only = opened_services[0]
    assert service_path == expected_path
    assert service_store is not None
    assert service_read_only is True


def _settings_for_daemon_lock(tmp_path) -> Settings:
    return Settings(
        home=tmp_path,
        db_path=tmp_path / ".data" / "agent_memory.db",
        export_dir=tmp_path / "exports",
        local_only=True,
        mcp_transport="stdio",
        mcp_host="127.0.0.1",
        mcp_port=8765,
        embedding_dims=16,
        embedding_model="hash-fallback",
        reranker_model="",
        vector_backend="sqlite",
        approval_mode="manual",
        owner_user_id="local",
        workspace_id="local",
        project_id="default",
        visibility_scope="private",
        sensitivity_level="normal",
        consensus_threshold=0.7,
        max_review_rounds=5,
        graph_path=tmp_path / ".graph" / "amo.kuzu",
        retrieval_db_path=tmp_path / ".data" / "retrieval.sqlite",
        evidence_dir=tmp_path / ".evidence",
    )
