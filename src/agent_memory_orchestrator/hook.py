from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import Settings
from .memory_service import MemoryService


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest one Claude/Codex hook payload")
    parser.add_argument("--agent", default="codex", choices=["claude", "codex", "user", "system"])
    parser.add_argument("--file", type=Path, help="JSON payload file. Defaults to stdin.")
    args = parser.parse_args(argv)

    raw = args.file.read_text(encoding="utf-8") if args.file else sys.stdin.read()
    payload = json.loads(raw or "{}")
    settings = Settings.load()
    svc = MemoryService(settings)
    try:
        svc.init_db()
        result = svc.codex_hook_response(payload, default_agent=args.agent)
        print(json.dumps(result, indent=2))
        return 0
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())
