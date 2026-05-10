from __future__ import annotations

import pytest

from agent_memory_orchestrator.app.daemon import _bounded_int, _load_web_asset, _web_asset_bytes


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

    assert "AMO Control Room" in html
    assert b"function buildGraph" in js
    assert content_type.startswith("application/javascript")


def test_web_asset_loader_blocks_path_traversal() -> None:
    with pytest.raises(ValueError):
        _web_asset_bytes("../daemon.py")
