"""Compatibility wrapper for the runtime MCP server."""

from __future__ import annotations

from ..runtime.mcp.server import create_server
from ..runtime.mcp.server import main

__all__ = ["create_server", "main"]


if __name__ == "__main__":
    main()
