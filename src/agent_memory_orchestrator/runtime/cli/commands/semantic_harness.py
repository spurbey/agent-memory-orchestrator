from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....application.services.semantic_harness import RepoBootstrapOptions
from ....application.services.semantic_harness import ShadowToolReplayService
from ....application.services.semantic_harness import SemanticHarnessRuntimeService
from ....application.services.semantic_harness import ToolContextPlanner
from ....core.config import Settings
from ....infrastructure.sqlite.semantic_harness import SQLiteHarnessGraphRepository
from ....infrastructure.sqlite.semantic_harness import SQLiteProjectionCache


def add_semantic_harness_subcommands(sub: Any) -> None:
    harness = sub.add_parser("amo-harness", help="Warm and evaluate the Semantic Harness graph")
    harness_sub = harness.add_subparsers(dest="amo_harness_command", required=True)
    _add_harness_actions(harness_sub)


def _add_harness_actions(harness_sub: Any) -> None:
    bootstrap = harness_sub.add_parser("bootstrap", help="Warm the Semantic Harness graph for one repository")
    bootstrap.add_argument("--repo", type=Path, required=True, help="Repository root to bootstrap.")
    bootstrap.add_argument("--repo-id", default="", help="Stable repo id. Defaults to deterministic repo root id.")
    bootstrap.add_argument("--db-path", type=Path, default=None, help="Override Semantic Harness SQLite path.")
    bootstrap.add_argument("--max-files", type=int, default=10_000, help="Maximum source files to read.")
    bootstrap.add_argument(
        "--include-untracked",
        action="store_true",
        help="Use filesystem walk instead of git ls-files for source discovery.",
    )
    replay = harness_sub.add_parser("shadow-replay", help="Replay PostToolUse evidence without injecting context")
    replay.add_argument("--repo-id", required=True, help="Repo id for the already-warmed harness graph.")
    replay.add_argument("--evidence", type=Path, default=None, help="Evidence JSONL to replay. Defaults to latest.")
    replay.add_argument("--db-path", type=Path, default=None, help="Override Semantic Harness SQLite path.")
    replay.add_argument("--limit", type=int, default=0, help="Maximum PostToolUse rows to replay. 0 means all.")
    replay.add_argument("--out", type=Path, default=None, help="Optional report JSON path.")


def handle_semantic_harness_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    if args.command != "amo-harness":
        return None
    if args.amo_harness_command == "bootstrap":
        settings = Settings.load()
        db_path = _semantic_harness_db_path(settings, getattr(args, "db_path", None))
        options = RepoBootstrapOptions(
            max_files=max(1, int(args.max_files)),
            prefer_git_tracked=not bool(args.include_untracked),
        )
        with SQLiteHarnessGraphRepository(db_path) as graph_repo:
            with SQLiteProjectionCache(db_path) as projection_cache:
                runtime = SemanticHarnessRuntimeService(
                    graph_repository=graph_repo,
                    projection_cache=projection_cache,
                )
                result = runtime.bootstrap_repo(args.repo, repo_id=args.repo_id, options=options)
        payload = result.as_dict()
        payload.update({"ok": True, "db_path": str(db_path), "command": "amo-harness bootstrap"})
        emit(payload)
        return 0
    if args.amo_harness_command == "shadow-replay":
        settings = Settings.load()
        db_path = _semantic_harness_db_path(settings, getattr(args, "db_path", None))
        evidence_path = getattr(args, "evidence", None) or _latest_evidence_file(settings)
        with SQLiteHarnessGraphRepository(db_path) as graph_repo:
            with SQLiteProjectionCache(db_path) as projection_cache:
                runtime = SemanticHarnessRuntimeService(
                    graph_repository=graph_repo,
                    projection_cache=projection_cache,
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
                "db_path": str(db_path),
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


def _semantic_harness_db_path(settings: Settings, override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    return (settings.home / ".data" / "semantic_harness.sqlite").resolve()


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
