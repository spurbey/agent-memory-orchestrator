"""Tests for runtime/codex_proxy/codex_config.py"""

from __future__ import annotations

import pytest

from agent_memory_orchestrator.runtime.codex_proxy.codex_config import (
    WrapResult,
    is_wrapped,
    unwrap,
    wrap,
)

PORT = 8766


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read(path):
    return path.read_text(encoding="utf-8")


def _has_top_level_keys(content: str) -> bool:
    return 'model_provider = "amo"' in content and "openai_base_url" in content


def _has_table(content: str) -> bool:
    return "[model_providers.amo]" in content


# ---------------------------------------------------------------------------
# Basic injection
# ---------------------------------------------------------------------------

def test_wrap_creates_file_when_missing(tmp_path):
    cfg = tmp_path / "config.toml"
    result = wrap(PORT, config_path=cfg)
    assert cfg.exists()
    assert isinstance(result, WrapResult)
    assert not result.already_present
    content = _read(cfg)
    assert _has_top_level_keys(content)
    assert _has_table(content)


def test_wrap_injects_correct_port(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    content = _read(cfg)
    assert f"http://127.0.0.1:{PORT}/v1" in content


def test_wrap_is_idempotent(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    content_after_first = _read(cfg)

    result2 = wrap(PORT, config_path=cfg)
    assert result2.already_present is True
    assert _read(cfg) == content_after_first


def test_wrap_replaces_stale_block_on_port_change(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    wrap(PORT + 1, config_path=cfg)
    content = _read(cfg)
    assert f"http://127.0.0.1:{PORT + 1}/v1" in content
    assert f"http://127.0.0.1:{PORT}/v1" not in content
    # Only one copy of each marker
    assert content.count("# BEGIN AMO PROXY KEYS") == 1
    assert content.count("# BEGIN AMO PROXY PROVIDER") == 1


def test_wrap_creates_snapshot_on_first_injection(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('existing = "value"\n', encoding="utf-8")
    result = wrap(PORT, config_path=cfg)
    assert result.snapshot_path is not None
    assert result.snapshot_path.exists()
    assert _read(result.snapshot_path) == 'existing = "value"\n'


def test_wrap_does_not_create_second_snapshot(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('existing = "value"\n', encoding="utf-8")
    result1 = wrap(PORT, config_path=cfg)
    result2 = wrap(PORT + 1, config_path=cfg)  # port change, not idempotent
    assert result1.snapshot_path is not None
    assert result2.snapshot_path is None  # no new snapshot


# ---------------------------------------------------------------------------
# TOML ordering: top-level keys must precede tables
# ---------------------------------------------------------------------------

def test_wrap_top_level_keys_before_existing_table(tmp_path):
    """model_provider must appear before any [table] in the file."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.foo]\ncommand = "x"\n', encoding="utf-8")
    wrap(PORT, config_path=cfg)
    content = _read(cfg)

    model_provider_pos = content.index('model_provider = "amo"')
    mcp_table_pos = content.index("[mcp_servers.foo]")
    assert model_provider_pos < mcp_table_pos, (
        "model_provider must appear before [mcp_servers.foo] in the file"
    )


def test_wrap_produces_valid_toml_with_existing_tables(tmp_path):
    """File must be parseable as TOML after wrapping."""
    tomllib = pytest.importorskip("tomllib")  # stdlib in 3.11+; skip if absent
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.foo]\ncommand = "x"\n', encoding="utf-8")
    wrap(PORT, config_path=cfg)
    content = _read(cfg)
    # Should not raise
    parsed = tomllib.loads(content)
    assert parsed["model_provider"] == "amo"
    assert "model_providers" in parsed
    assert parsed["model_providers"]["amo"]["supports_websockets"] is True


# ---------------------------------------------------------------------------
# is_wrapped
# ---------------------------------------------------------------------------

def test_is_wrapped_false_before_wrap(tmp_path):
    cfg = tmp_path / "config.toml"
    assert is_wrapped(config_path=cfg) is False


def test_is_wrapped_true_after_wrap(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    assert is_wrapped(config_path=cfg) is True


def test_is_wrapped_false_after_unwrap(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    unwrap(config_path=cfg)
    assert is_wrapped(config_path=cfg) is False


# ---------------------------------------------------------------------------
# unwrap — default (strip markers, preserve post-wrap edits)
# ---------------------------------------------------------------------------

def test_unwrap_strips_amo_blocks(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    changed = unwrap(config_path=cfg)
    assert changed is True
    content = _read(cfg)
    assert "BEGIN AMO" not in content
    assert "END AMO" not in content
    assert "[model_providers.amo]" not in content


def test_unwrap_preserves_existing_content(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('existing = "value"\n', encoding="utf-8")
    wrap(PORT, config_path=cfg)
    unwrap(config_path=cfg)
    assert 'existing = "value"' in _read(cfg)


def test_unwrap_preserves_post_wrap_edits(tmp_path):
    """Default unwrap must NOT discard changes made after wrap()."""
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    # Simulate user adding something after wrap
    with cfg.open("a", encoding="utf-8") as f:
        f.write('\n[user_added]\nkey = "after"\n')
    unwrap(config_path=cfg)
    content = _read(cfg)
    assert 'key = "after"' in content


def test_unwrap_returns_false_when_nothing_to_strip(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('existing = "value"\n', encoding="utf-8")
    assert unwrap(config_path=cfg) is False


def test_unwrap_returns_false_on_missing_file(tmp_path):
    cfg = tmp_path / "config.toml"
    assert unwrap(config_path=cfg) is False


def test_unwrap_deletes_snapshot(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('existing = "value"\n', encoding="utf-8")
    result = wrap(PORT, config_path=cfg)
    assert result.snapshot_path is not None and result.snapshot_path.exists()
    unwrap(config_path=cfg)
    assert not result.snapshot_path.exists()


# ---------------------------------------------------------------------------
# unwrap(restore_snapshot=True) — byte-for-byte restore
# ---------------------------------------------------------------------------

def test_unwrap_restore_snapshot_restores_original(tmp_path):
    cfg = tmp_path / "config.toml"
    original = 'existing = "value"\n'
    cfg.write_text(original, encoding="utf-8")
    wrap(PORT, config_path=cfg)
    # Simulate post-wrap edit that will be discarded
    with cfg.open("a", encoding="utf-8") as f:
        f.write('\n[user_added]\nkey = "after"\n')
    unwrap(config_path=cfg, restore_snapshot=True)
    assert _read(cfg) == original


def test_unwrap_restore_snapshot_deletes_snapshot(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('x = 1\n', encoding="utf-8")
    result = wrap(PORT, config_path=cfg)
    snapshot = result.snapshot_path
    unwrap(config_path=cfg, restore_snapshot=True)
    assert not snapshot.exists()


def test_unwrap_restore_snapshot_raises_when_no_snapshot(tmp_path):
    cfg = tmp_path / "config.toml"
    wrap(PORT, config_path=cfg)
    # Manually delete snapshot
    snap = cfg.with_suffix(".toml.amo-snapshot")
    if snap.exists():
        snap.unlink()
    with pytest.raises(FileNotFoundError):
        unwrap(config_path=cfg, restore_snapshot=True)
