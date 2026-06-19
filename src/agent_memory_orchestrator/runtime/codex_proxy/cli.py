"""CLI entry point for amo-proxy."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="AMO Semantic Proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--openai-url", default=None, help="Upstream OpenAI base URL")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print(
            "uvicorn is not installed. Install the proxy extra:\n"
            "  pip install agent-memory-orchestrator[proxy]",
            file=sys.stderr,
        )
        sys.exit(1)

    from .server import create_app

    app = create_app(upstream_base_url=args.openai_url)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
