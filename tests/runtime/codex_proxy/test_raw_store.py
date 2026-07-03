"""Tests for runtime/codex_proxy/raw_store.py"""

from __future__ import annotations

from agent_memory_orchestrator.runtime.codex_proxy.raw_store import ProxyRawOutputStore


def test_save_writes_file(tmp_path):
    store = ProxyRawOutputStore(root=tmp_path)
    ref = "sha256:" + "a" * 64
    assert store.save(ref, "hello output") is True
    assert (tmp_path / ("a" * 64 + ".txt")).read_text() == "hello output"


def test_save_is_idempotent(tmp_path):
    store = ProxyRawOutputStore(root=tmp_path)
    ref = "sha256:" + "b" * 64
    store.save(ref, "first")
    store.save(ref, "second")
    # second write is a no-op — file keeps first content
    assert (tmp_path / ("b" * 64 + ".txt")).read_text() == "first"


def test_save_invalid_ref_returns_false(tmp_path):
    store = ProxyRawOutputStore(root=tmp_path)
    assert store.save("notsha256:abc", "data") is False
    assert store.save("", "data") is False
    assert store.save("sha256:", "data") is False


def test_save_path_traversal_returns_false(tmp_path):
    store = ProxyRawOutputStore(root=tmp_path)
    assert store.save("sha256:../../etc/passwd", "bad") is False
    assert store.save("sha256:sub/dir", "bad") is False
    assert store.save("sha256:with.dot", "bad") is False


def test_save_failure_returns_false(tmp_path):
    # Make root a file so mkdir fails
    bad_root = tmp_path / "file.txt"
    bad_root.write_text("x")
    store = ProxyRawOutputStore(root=bad_root)
    ref = "sha256:" + "c" * 64
    assert store.save(ref, "data") is False


def test_exists_returns_true_after_save(tmp_path):
    store = ProxyRawOutputStore(root=tmp_path)
    ref = "sha256:" + "d" * 64
    store.save(ref, "text")
    assert store.exists(ref) is True


def test_exists_returns_false_before_save(tmp_path):
    store = ProxyRawOutputStore(root=tmp_path)
    ref = "sha256:" + "e" * 64
    assert store.exists(ref) is False


def test_default_root_uses_proxy_raw_store_env(monkeypatch, tmp_path):
    root = tmp_path / "proxy-raw"
    monkeypatch.setenv("AMO_PROXY_RAW_STORE_DIR", str(root))
    store = ProxyRawOutputStore()
    ref = "sha256:" + "f" * 64

    assert store.save(ref, "env output") is True
    assert (root / ("f" * 64 + ".txt")).read_text(encoding="utf-8") == "env output"


def test_default_root_uses_amo_home(monkeypatch, tmp_path):
    amo_home = tmp_path / "amo-home"
    monkeypatch.delenv("AMO_PROXY_RAW_STORE_DIR", raising=False)
    monkeypatch.setenv("AMO_HOME", str(amo_home))
    store = ProxyRawOutputStore()
    ref = "sha256:" + "1" * 64

    assert store.save(ref, "home output") is True
    assert (amo_home / ".proxy" / "raw_outputs" / ("1" * 64 + ".txt")).read_text(encoding="utf-8") == "home output"
