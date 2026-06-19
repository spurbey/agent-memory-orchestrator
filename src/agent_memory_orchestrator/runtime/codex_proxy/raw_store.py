"""Proxy raw output store adapter.

Owns: persist raw tool output to filesystem by sha256 raw_ref.
Does NOT own SQL, evidence pipeline, or graph logic.

Default location: <AMO_HOME>/.proxy/raw_outputs/
Tests should inject a tmp_path via ProxyRawOutputStore(root=...).
"""

from __future__ import annotations

import os
from pathlib import Path


def _default_root() -> Path:
    explicit = os.environ.get("AMO_PROXY_RAW_STORE_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    amo_home = Path(os.environ.get("AMO_HOME", str(Path.home() / ".agent-memory-orchestrator")))
    return amo_home.expanduser().resolve() / ".proxy" / "raw_outputs"


class ProxyRawOutputStore:
    def __init__(self, root: Path | None = None) -> None:
        self._root = (root or _default_root()).expanduser().resolve()

    def save(self, raw_ref: str, text: str) -> bool:
        """Write raw tool output to disk keyed by raw_ref.

        Returns True on success, False on any failure (invalid ref, I/O error).
        Never raises to caller.
        """
        try:
            if not isinstance(raw_ref, str) or not raw_ref.startswith("sha256:"):
                return False
            hex_part = raw_ref[len("sha256:"):]
            # Guard against path traversal
            if not hex_part or "/" in hex_part or "\\" in hex_part or "." in hex_part:
                return False
            self._root.mkdir(parents=True, exist_ok=True)
            dest = self._root / f"{hex_part}.txt"
            if not dest.exists():
                dest.write_text(str(text or ""), encoding="utf-8")
            return True
        except Exception:
            return False

    def exists(self, raw_ref: str) -> bool:
        try:
            if not isinstance(raw_ref, str) or not raw_ref.startswith("sha256:"):
                return False
            hex_part = raw_ref[len("sha256:"):]
            if not hex_part or "/" in hex_part or "\\" in hex_part or "." in hex_part:
                return False
            return (self._root / f"{hex_part}.txt").exists()
        except Exception:
            return False


__all__ = ["ProxyRawOutputStore"]
