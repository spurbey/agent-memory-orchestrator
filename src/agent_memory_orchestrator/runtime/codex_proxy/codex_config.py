"""Codex config.toml wrapper for the AMO proxy provider.

Owns: snapshot, inject, idempotent re-inject, unwrap/strip.
Does NOT own HTTP transport, payload mutation, or ranking.

Two marker blocks are used to keep TOML ordering valid:

  Block A — injected at the TOP (before any existing tables):
    # BEGIN AMO PROXY KEYS
    model_provider = "amo"
    openai_base_url = "http://127.0.0.1:<port>/v1"
    # END AMO PROXY KEYS

  Block B — appended at the END (after all existing content):
    # BEGIN AMO PROXY PROVIDER
    [model_providers.amo]
    name = "AMO Semantic Proxy"
    base_url = "http://127.0.0.1:<port>/v1"
    supports_websockets = true
    # END AMO PROXY PROVIDER

TOML requires bare top-level keys to appear before table headers.
Splitting into two blocks guarantees this regardless of what the
existing config already contains.

Unwrap behaviour
----------------
Default unwrap() strips both AMO marker blocks from the current file.
This preserves any user edits made after wrapping.

unwrap(restore_snapshot=True) restores the snapshot byte-for-byte,
discarding all post-wrap edits. The caller opts in explicitly.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

# Marker pair A — top-level keys (inserted at file top)
_KEYS_BEGIN = "# BEGIN AMO PROXY KEYS"
_KEYS_END = "# END AMO PROXY KEYS"
_KEYS_RE = re.compile(
    rf"{re.escape(_KEYS_BEGIN)}\n.*?{re.escape(_KEYS_END)}\n?",
    re.DOTALL,
)

# Marker pair B — table section (appended at file end)
_TABLE_BEGIN = "# BEGIN AMO PROXY PROVIDER"
_TABLE_END = "# END AMO PROXY PROVIDER"
_TABLE_RE = re.compile(
    rf"{re.escape(_TABLE_BEGIN)}\n.*?{re.escape(_TABLE_END)}\n?",
    re.DOTALL,
)

_DEFAULT_CONFIG = Path.home() / ".codex" / "config.toml"


@dataclass(frozen=True)
class WrapResult:
    config_path: Path
    snapshot_path: Path | None  # None when already_present or snapshot already existed
    already_present: bool


def wrap(port: int, *, config_path: Path = _DEFAULT_CONFIG) -> WrapResult:
    """Inject AMO proxy blocks into ~/.codex/config.toml.

    Block A (top-level keys) is prepended before all existing content.
    Block B ([model_providers.amo] table) is appended after all existing content.

    Idempotent: if both blocks are already present with the same port, no-op.
    If port changed, replaces both stale blocks without creating a new snapshot.
    Creates config_path and parent dirs if they do not exist yet.
    """
    config_path.parent.mkdir(parents=True, exist_ok=True)
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""

    keys_block = _build_keys_block(port)
    table_block = _build_table_block(port)

    already_has_keys = _KEYS_BEGIN in existing
    already_has_table = _TABLE_BEGIN in existing

    if already_has_keys and already_has_table:
        # Check if port is already correct in both blocks
        current_keys_match = _KEYS_RE.search(existing)
        current_table_match = _TABLE_RE.search(existing)
        keys_ok = current_keys_match and current_keys_match.group(0).strip() == keys_block.strip()
        table_ok = current_table_match and current_table_match.group(0).strip() == table_block.strip()
        if keys_ok and table_ok:
            return WrapResult(config_path=config_path, snapshot_path=None, already_present=True)
        # Port changed — replace both stale blocks, no new snapshot
        stripped = _strip_amo_blocks(existing)
        updated = _assemble(stripped, keys_block, table_block)
        config_path.write_text(updated, encoding="utf-8")
        return WrapResult(config_path=config_path, snapshot_path=None, already_present=False)

    # First injection — snapshot the original file before touching it
    snapshot_path: Path | None = None
    snapshot_candidate = config_path.with_suffix(".toml.amo-snapshot")
    if config_path.exists() and not snapshot_candidate.exists():
        shutil.copy2(config_path, snapshot_candidate)
        snapshot_path = snapshot_candidate

    # Strip any partial leftover blocks before re-injecting
    stripped = _strip_amo_blocks(existing)
    updated = _assemble(stripped, keys_block, table_block)
    config_path.write_text(updated, encoding="utf-8")
    return WrapResult(config_path=config_path, snapshot_path=snapshot_path, already_present=False)


def unwrap(*, config_path: Path = _DEFAULT_CONFIG, restore_snapshot: bool = False) -> bool:
    """Remove the AMO proxy blocks from ~/.codex/config.toml.

    Default (restore_snapshot=False):
        Strips both AMO marker blocks from the current file.
        Preserves any user edits made after wrapping.

    restore_snapshot=True:
        Restores the snapshot byte-for-byte (discards post-wrap edits).
        Use only when you are sure no important edits were made after wrap().

    Returns True when the file was modified.
    """
    snapshot_path = config_path.with_suffix(".toml.amo-snapshot")

    if restore_snapshot:
        if not snapshot_path.exists():
            raise FileNotFoundError(f"No AMO snapshot found at {snapshot_path}")
        shutil.copy2(snapshot_path, config_path)
        snapshot_path.unlink()
        return True

    if not config_path.exists():
        return False

    content = config_path.read_text(encoding="utf-8")
    if _KEYS_BEGIN not in content and _TABLE_BEGIN not in content:
        return False

    updated = _strip_amo_blocks(content).rstrip("\n") + "\n"
    config_path.write_text(updated, encoding="utf-8")

    # Clean up orphaned snapshot if present
    if snapshot_path.exists():
        snapshot_path.unlink()

    return True


def is_wrapped(*, config_path: Path = _DEFAULT_CONFIG) -> bool:
    """Return True if either AMO marker block is present in config_path."""
    if not config_path.exists():
        return False
    content = config_path.read_text(encoding="utf-8")
    return _KEYS_BEGIN in content or _TABLE_BEGIN in content


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _proxy_base_url(port: int) -> str:
    return f"http://127.0.0.1:{port}/v1"


def _build_keys_block(port: int) -> str:
    base = _proxy_base_url(port)
    return (
        f"{_KEYS_BEGIN}\n"
        'model_provider = "amo"\n'
        f'openai_base_url = "{base}"\n'
        f"{_KEYS_END}\n"
    )


def _build_table_block(port: int) -> str:
    base = _proxy_base_url(port)
    return (
        f"{_TABLE_BEGIN}\n"
        "[model_providers.amo]\n"
        'name = "AMO Semantic Proxy"\n'
        f'base_url = "{base}"\n'
        "supports_websockets = true\n"
        f"{_TABLE_END}\n"
    )


def _strip_amo_blocks(content: str) -> str:
    content = _KEYS_RE.sub("", content)
    content = _TABLE_RE.sub("", content)
    return content


def _assemble(existing_stripped: str, keys_block: str, table_block: str) -> str:
    """Prepend keys_block before existing content, append table_block after."""
    middle = existing_stripped.strip("\n")
    if middle:
        return keys_block + "\n" + middle + "\n\n" + table_block
    return keys_block + "\n" + table_block


__all__ = ["WrapResult", "wrap", "unwrap", "is_wrapped"]
