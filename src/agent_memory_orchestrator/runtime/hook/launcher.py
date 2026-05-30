"""Console-script adapter for the AMO hook runtime."""

from __future__ import annotations

from ...app import hook as _impl

main = _impl.main

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
