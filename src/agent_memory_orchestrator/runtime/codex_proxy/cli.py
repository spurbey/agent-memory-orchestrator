"""CLI entry point for amo-proxy."""

from __future__ import annotations

import argparse
import os
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="AMO Semantic Proxy")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--openai-url", default=None, help="Upstream OpenAI base URL")
    parser.add_argument("--mutate", action="store_true", help="Enable tool-output mutation (AMO_PROXY_MUTATE=1)")
    parser.add_argument("--repo-id", default=None, help="Repo id for the warmed Semantic Harness graph")
    parser.add_argument(
        "--wrap-codex-config",
        action="store_true",
        help="Inject AMO proxy blocks into ~/.codex/config.toml before starting",
    )
    parser.add_argument(
        "--unwrap-codex-config",
        action="store_true",
        help="Remove AMO proxy blocks from ~/.codex/config.toml and exit",
    )
    args = parser.parse_args()

    from .codex_config import unwrap, wrap

    if args.unwrap_codex_config:
        changed = unwrap()
        print("unwrapped ~/.codex/config.toml" if changed else "nothing to unwrap")
        sys.exit(0)

    if args.wrap_codex_config:
        result = wrap(args.port)
        if result.already_present:
            print(f"~/.codex/config.toml already points to port {args.port}")
        else:
            snap = f" (snapshot: {result.snapshot_path})" if result.snapshot_path else ""
            print(f"wrapped ~/.codex/config.toml → port {args.port}{snap}")

    if args.mutate:
        os.environ["AMO_PROXY_MUTATE"] = "1"
    if args.repo_id:
        os.environ["AMO_PROXY_REPO_ID"] = args.repo_id

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
