from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....application.services.semantic_harness import RepoBootstrapOptions
from ....application.services.semantic_harness import InMemoryProjectionCache
from ....application.services.semantic_harness import ShadowToolReplayService
from ....application.services.semantic_harness import SemanticHarnessRuntimeService
from ....application.services.semantic_harness import ToolContextPlanner
from ....core.config import Settings
from ....infrastructure.helixdb.semantic_harness import HelixHarnessConfig
from ....infrastructure.helixdb.semantic_harness import HelixHarnessGraphRepository
from ....infrastructure.helixdb.semantic_harness import migrate_sqlite_repo_to_helix


def add_semantic_harness_subcommands(sub: Any) -> None:
    harness = sub.add_parser("amo-harness", help="Warm and evaluate the Semantic Harness graph")
    harness_sub = harness.add_subparsers(dest="amo_harness_command", required=True)
    _add_harness_actions(harness_sub)


def _add_harness_actions(harness_sub: Any) -> None:
    bootstrap = harness_sub.add_parser("bootstrap", help="Warm the Semantic Harness graph for one repository")
    bootstrap.add_argument("--repo", type=Path, required=True, help="Repository root to bootstrap.")
    bootstrap.add_argument("--repo-id", default="", help="Stable repo id. Defaults to deterministic repo root id.")
    bootstrap.add_argument("--helix-url", default=None, help="Override local HelixDB URL.")
    bootstrap.add_argument("--max-files", type=int, default=10_000, help="Maximum source files to read.")
    bootstrap.add_argument(
        "--include-untracked",
        action="store_true",
        help="Use filesystem walk instead of git ls-files for source discovery.",
    )
    replay = harness_sub.add_parser("shadow-replay", help="Replay PostToolUse evidence without injecting context")
    replay.add_argument("--repo-id", required=True, help="Repo id for the already-warmed harness graph.")
    replay.add_argument("--evidence", type=Path, default=None, help="Evidence JSONL to replay. Defaults to latest.")
    replay.add_argument("--helix-url", default=None, help="Override local HelixDB URL.")
    replay.add_argument("--limit", type=int, default=0, help="Maximum PostToolUse rows to replay. 0 means all.")
    replay.add_argument("--out", type=Path, default=None, help="Optional report JSON path.")
    migrate = harness_sub.add_parser("migrate-sqlite-to-helix", help="One-time Semantic Harness graph migration")
    migrate.add_argument("--repo-id", required=True, help="Legacy SQLite repo id to migrate.")
    migrate.add_argument("--db-path", type=Path, required=True, help="Legacy Semantic Harness SQLite path.")
    migrate.add_argument("--helix-url", default=None, help="Override local HelixDB URL.")


def handle_semantic_harness_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    if args.command != "amo-harness":
        return None
    if args.amo_harness_command == "bootstrap":
        config = _helix_config(getattr(args, "helix_url", None))
        options = RepoBootstrapOptions(
            max_files=max(1, int(args.max_files)),
            prefer_git_tracked=not bool(args.include_untracked),
        )
        with HelixHarnessGraphRepository(config) as graph_repo:
            runtime = SemanticHarnessRuntimeService(
                graph_repository=graph_repo,
                projection_cache=InMemoryProjectionCache(),
            )
            result = runtime.bootstrap_repo(args.repo, repo_id=args.repo_id, options=options)
        payload = result.as_dict()
        payload.update({"ok": True, "backend": "helix", "helix_url": config.url, "command": "amo-harness bootstrap"})
        emit(payload)
        return 0
    if args.amo_harness_command == "shadow-replay":
        settings = Settings.load()
        config = _helix_config(getattr(args, "helix_url", None))
        evidence_path = getattr(args, "evidence", None) or _latest_evidence_file(settings)
        with HelixHarnessGraphRepository(config) as graph_repo:
            runtime = SemanticHarnessRuntimeService(
                graph_repository=graph_repo,
                projection_cache=InMemoryProjectionCache(),
            )
            planner = ToolContextPlanner(runtime=runtime)
            report = ShadowToolReplayService(planner=planner).replay_file(
                evidence_path,
                repo_id=args.repo_id,
                limit=max(0, int(args.limit)),
            )
        payload = report.as_dict()
        payload.update(
            {
                "ok": True,
                "backend": "helix",
                "helix_url": config.url,
                "command": "amo-harness shadow-replay",
                "shadow_only": True,
            }
        )
        if args.out is not None:
            out_path = args.out.expanduser().resolve()
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            payload["out"] = str(out_path)
        emit(payload)
        return 0
    if args.amo_harness_command == "migrate-sqlite-to-helix":
        config = _helix_config(getattr(args, "helix_url", None))
        payload = migrate_sqlite_repo_to_helix(
            repo_id=args.repo_id,
            sqlite_path=args.db_path.expanduser().resolve(),
            helix_config=config,
        )
        payload.update({"ok": True, "command": "amo-harness migrate-sqlite-to-helix"})
        emit(payload)
        return 0
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Semantic Harness evaluation CLI")
    sub = parser.add_subparsers(dest="amo_harness_command", required=True)
    _add_harness_actions(sub)
    args = parser.parse_args(argv)
    args.command = "amo-harness"

    def _emit(payload: object) -> None:
        print(json.dumps(payload, indent=2))

    status = handle_semantic_harness_command(args, emit=_emit)
    if status is not None:
        return status
    parser.error(f"unknown command: {args.command}")
    return 2


def _helix_config(override: str | None) -> HelixHarnessConfig:
    config = HelixHarnessConfig.from_env()
    return HelixHarnessConfig(url=str(override).rstrip("/"), batch_size=config.batch_size) if override else config


def _latest_evidence_file(settings: Settings) -> Path:
    files = sorted(
        (path for path in settings.evidence_dir.glob("*.jsonl") if path.is_file() and not path.name.endswith(".lock")),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not files:
        raise FileNotFoundError(f"No evidence JSONL files found under {settings.evidence_dir}")
    return files[0].resolve()


__all__ = [
    "add_semantic_harness_subcommands",
    "handle_semantic_harness_command",
    "main",
]
