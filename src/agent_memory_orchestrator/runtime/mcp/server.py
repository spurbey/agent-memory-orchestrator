"""Console-script adapter for the AMO MCP server runtime."""

from __future__ import annotations

from ...mcp import server as _impl

create_server = _impl.create_server
main = _impl.main

__all__ = ["create_server", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
