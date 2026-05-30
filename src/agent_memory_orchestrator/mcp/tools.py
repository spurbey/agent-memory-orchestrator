"""Compatibility wrapper for runtime MCP tools."""

from __future__ import annotations

from ..runtime.mcp import tools as _impl

for _name in dir(_impl):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_impl, _name)

__all__ = [name for name in globals() if not name.startswith("_")]
