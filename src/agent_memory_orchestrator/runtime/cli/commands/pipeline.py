"""CLI command group handling for production pipeline operations."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....reasoning_graph.central_merge.applier import apply_merge_plan
from ....reasoning_graph.central_merge.backfill import backfill_central_merge_plan
from ....reasoning_graph.central_merge.fixtures import export_job_fixture
from ....reasoning_graph.central_merge.judge import run_semantic_eval_fixture
from ....reasoning_graph.central_merge.production_eval import default_production_eval_path
from ....reasoning_graph.central_merge.production_eval import run_production_semantic_eval
from ....reasoning_graph.jobs.reset import adopt_existing_production_storage
from ....reasoning_graph.jobs.reset import initialize_fresh_production_storage
from ....reasoning_graph.jobs.reset import reset_production_storage
from ....reasoning_graph.jobs.store import ProductionSessionJobStore

PIPELINE_COMMANDS = (
    "init-production",
    "reset-production",
    "adopt-production",
    "production",
)

PRODUCTION_SUBCOMMANDS = (
    "export-fixture",
    "semantic-eval",
    "eval",
    "merge-plan",
    "merge-apply",
)


def handle_pipeline_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run production-pipeline commands.

    Returns ``None`` when ``args`` is not part of this command group so the
    top-level CLI can continue dispatching other groups.
    """
    if args.command == "init-production":
        settings = Settings.load()
        emit(initialize_fresh_production_storage(settings))
        return 0

    if args.command == "reset-production":
        settings = Settings.load()
        result = reset_production_storage(
            settings,
            backup=args.backup,
            clean_graph=args.clean_graph,
            clean_retrieval=args.clean_retrieval,
            force_if_daemon_running=args.force_if_daemon_running,
        )
        emit(result)
        return 0

    if args.command == "adopt-production":
        settings = Settings.load()
        result = adopt_existing_production_storage(
            settings,
            backup=args.backup,
            validate_graph=args.validate_graph,
            validate_retrieval=args.validate_retrieval,
            force_if_daemon_running=args.force_if_daemon_running,
        )
        emit(result)
        return 0

    if args.command != "production":
        return None

    if args.production_command == "export-fixture":
        settings = Settings.load()
        result = export_job_fixture(settings, job_id=args.job_id, out_dir=args.out, copy_artifacts=args.copy_artifacts)
        emit({"ok": result["ok"], "path": result["path"], "fixture": result["fixture"]})
        return 0

    if args.production_command == "semantic-eval":
        settings = Settings.load()
        result = run_semantic_eval_fixture(fixture_path=args.fixture, case_set=args.case_set)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
            result = {**result, "path": str(args.out)}
        store = ProductionSessionJobStore(settings)
        try:
            store.record_semantic_eval_run(
                run_id=f"production-eval:{int(time.time() * 1000)}",
                case_set=args.case_set,
                fixture_path=str(args.fixture),
                status=str(result.get("status") or "invalid"),
                metrics=result.get("metrics") if isinstance(result.get("metrics"), dict) else {},
                diagnostics={"judge_mode": "fixture_semantic_rubric_v1"},
            )
        finally:
            store.close()
        emit(result)
        return 0 if result.get("status") == "passed" else 1

    if args.production_command == "eval":
        settings = Settings.load()
        out_path = args.out or default_production_eval_path(Path.cwd())
        result = run_production_semantic_eval(
            settings,
            job_id=args.job_id,
            repo_id=args.repo_id,
            mode=args.mode,
            out_path=out_path,
        )
        emit(result)
        return 0

    if args.production_command == "merge-plan":
        settings = Settings.load()
        if args.backfill:
            result = backfill_central_merge_plan(settings, job_id=args.job_id, forced_by=args.forced_by)
            emit(result)
            return 0 if result.get("ok") else 1
        store = ProductionSessionJobStore(settings)
        try:
            plan = store.get_central_merge_plan_for_job(args.job_id)
            candidates = store.list_review_candidates(plan_id=str(plan["plan_id"])) if plan else []
        finally:
            store.close()
        emit({"ok": plan is not None, "plan": plan, "review_candidates": candidates})
        return 0 if plan is not None else 1

    if args.production_command == "merge-apply":
        settings = Settings.load()
        result = apply_merge_plan(settings=settings, plan_id=args.plan_id, branch=args.branch, mode=args.view)
        emit(result)
        return 0 if result.get("ok") else 1

    return None


__all__ = ["PIPELINE_COMMANDS", "PRODUCTION_SUBCOMMANDS", "handle_pipeline_command"]
