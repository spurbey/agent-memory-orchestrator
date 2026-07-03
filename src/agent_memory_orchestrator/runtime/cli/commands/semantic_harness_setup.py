from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....application.services.semantic_harness import InMemoryProjectionCache
from ....application.services.semantic_harness import RepoBootstrapOptions
from ....application.services.semantic_harness import SemanticHarnessRuntimeService
from ....core.config import Settings
from ....domain.semantic_harness import repo_id_for_root
from ....infrastructure.helixdb.local_runtime import LocalHelixRuntimeConfig
from ....infrastructure.helixdb.local_runtime import ensure_local_helix
from ....infrastructure.helixdb.local_runtime import local_helix_status
from ....infrastructure.helixdb.semantic_harness import HelixHarnessConfig
from ....infrastructure.helixdb.semantic_harness import HelixHarnessGraphRepository
from ....infrastructure.helixdb.semantic_harness import migrate_sqlite_repo_to_helix


def add_helix_setup_actions(harness_sub: Any) -> None:
    setup = harness_sub.add_parser("setup", help="Install/start HelixDB and warm one repository in one command")
    setup.add_argument("--repo", type=Path, required=True, help="Repository root to migrate or bootstrap.")
    setup.add_argument("--repo-id", default="", help="Stable repo id. Defaults to deterministic repo root id.")
    setup.add_argument("--legacy-db", type=Path, default=None, help="Optional legacy Semantic Harness SQLite path.")
    setup.add_argument("--rebuild", action="store_true", help="Explicitly replace an existing Helix graph from source.")
    setup.add_argument("--max-files", type=int, default=10_000, help="Maximum source files for a fresh bootstrap.")
    setup.add_argument("--include-untracked", action="store_true", help="Use filesystem walk for a fresh bootstrap.")

    harness_sub.add_parser("helix-status", help="Report local HelixDB runtime health")


def handle_helix_setup_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    if args.command != "amo-harness" or args.amo_harness_command not in {"setup", "helix-status"}:
        return None
    settings = Settings.load()
    local = LocalHelixRuntimeConfig(home=settings.home)
    if args.amo_harness_command == "helix-status":
        payload = local_helix_status(local)
        payload.update({"ok": payload["status"] == "ready", "command": "amo-harness helix-status"})
        emit(payload)
        return 0 if payload["ok"] else 1

    runtime = ensure_local_helix(local)
    repo_root = args.repo.expanduser().resolve()
    repo_id = str(args.repo_id or "").strip() or repo_id_for_root(repo_root)
    helix_config = HelixHarnessConfig(url=local.url)
    with HelixHarnessGraphRepository(helix_config) as repository:
        existing = repository.load(repo_id)
    if existing is not None and not args.rebuild:
        emit(
            {
                "ok": True,
                "command": "amo-harness setup",
                "repo_id": repo_id,
                "graph_action": "reused_existing",
                "helix": runtime,
            }
        )
        return 0

    legacy_db = _legacy_db(settings, args.legacy_db)
    if existing is None and legacy_db.is_file():
        try:
            migrated = migrate_sqlite_repo_to_helix(
                repo_id=repo_id,
                sqlite_path=legacy_db,
                helix_config=helix_config,
            )
        except ValueError as exc:
            if not str(exc).startswith("sqlite_repo_not_found:"):
                raise
        else:
            emit(
                {
                    "ok": True,
                    "command": "amo-harness setup",
                    "repo_id": repo_id,
                    "graph_action": "migrated_legacy",
                    "migration": migrated,
                    "helix": runtime,
                }
            )
            return 0

    with HelixHarnessGraphRepository(helix_config) as repository:
        service = SemanticHarnessRuntimeService(
            graph_repository=repository,
            projection_cache=InMemoryProjectionCache(),
        )
        bootstrap = service.bootstrap_repo(
            repo_root,
            repo_id=repo_id,
            options=RepoBootstrapOptions(
                max_files=max(1, int(args.max_files)),
                prefer_git_tracked=not bool(args.include_untracked),
            ),
        )
    emit(
        {
            "ok": True,
            "command": "amo-harness setup",
            "repo_id": repo_id,
            "graph_action": "rebuilt" if existing is not None else "bootstrapped",
            "bootstrap": bootstrap.as_dict(),
            "helix": runtime,
        }
    )
    return 0


def _legacy_db(settings: Settings, override: Path | None) -> Path:
    if override is not None:
        return override.expanduser().resolve()
    return (settings.home / ".data" / "semantic_harness.sqlite").resolve()


__all__ = ["add_helix_setup_actions", "handle_helix_setup_command"]
