"""Compatibility wrapper for the runtime CLI implementation."""

from __future__ import annotations

import sys

from ..runtime.cli import main as _impl

if __name__ == "__main__":
    raise SystemExit(_impl.main())

sys.modules[__name__] = _impl
