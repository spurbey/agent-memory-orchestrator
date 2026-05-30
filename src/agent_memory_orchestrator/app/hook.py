"""Compatibility wrapper for the runtime hook launcher."""

from __future__ import annotations

from ..runtime.hook import launcher as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

__all__ = [name for name in globals() if not name.startswith("_")]


if __name__ == "__main__":
    raise SystemExit(_impl.main())
