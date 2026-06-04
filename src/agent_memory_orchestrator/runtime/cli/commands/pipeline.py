"""CLI command group handling for production pipeline operations."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....application.services.central_merge.apply import apply_merge_plan
from ....application.pipeline.debug.backfill import backfill_central_merge_plan
from ....application.pipeline.debug.fixtures import export_job_fixture
from ....application.pipeline.evaluation.production_eval import default_production_eval_path
from ....application.pipeline.evaluation.production_eval import DEFAULT_TARGET_JOB_ID
from ....application.pipeline.evaluation.production_eval import DEFAULT_TARGET_REPO_ID
from ....application.pipeline.evaluation.production_eval import run_production_semantic_eval
from ....application.pipeline.evaluation.semantic_fixture import run_semantic_eval_fixture
from ....application.pipeline.storage_lifecycle import adopt_existing_production_storage
from ....application.pipeline.storage_lifecycle import initialize_fresh_production_storage
from ....application.pipeline.storage_lifecycle import reset_production_storage
from ....infrastructure.sqlite.production_job_store import ProductionSessionJobStore
from ...daemon.status import RuntimeDaemonStatus

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


def add_pipeline_subcommands(sub: Any) -> None:
    sub.add_parser(
        "init-production",
        help="Non-destructively mark empty fresh graph/retrieval stores as production-ready",
    )

    prod_reset = sub.add_parser("reset-production", help="Explicitly back up and reset production graph/retrieval stores")
    prod_reset.add_argument("--backup", action="store_true", help="Required. Create a timestamped backup before cleaning.")
    prod_reset.add_argument("--clean-graph", action="store_true", help="Clean/recreate the production Kuzu graph store.")
    prod_reset.add_argument("--clean-retrieval", action="store_true", help="Clean retrieval docs/vector ledger and FAISS cache.")
    prod_reset.add_argument(
        "--force-if-daemon-running",
        action="store_true",
        help="Allow reset even if the daemon health endpoint is reachable.",
    )
    prod_adopt = sub.add_parser(
        "adopt-production",
        help="Back up and mark existing production graph/retrieval stores as runner-ready without deleting them",
    )
    prod_adopt.add_argument("--backup", action="store_true", help="Required. Create a timestamped backup before adoption.")
    prod_adopt.add_argument("--validate-graph", action="store_true", help="Required. Verify the production graph store exists.")
    prod_adopt.add_argument("--validate-retrieval", action="store_true", help="Required. Verify retrieval documents exist.")
    prod_adopt.add_argument(
        "--force-if-daemon-running",
        action="store_true",
        help="Allow adoption even if the daemon health endpoint is reachable.",
    )
    production = sub.add_parser("production", help="Production job, fixture, semantic eval, and central merge commands")
    production_sub = production.add_subparsers(dest="production_command", required=True)
    prod_export_nested = production_sub.add_parser("export-fixture", help="Export a production job fixture for semantic evaluation")
    prod_export_nested.add_argument("--job-id", required=True)
    prod_export_nested.add_argument("--out", type=Path, help="Output directory for fixture.json")
    prod_export_nested.add_argument("--copy-artifacts", action="store_true", help="Copy stage output artifacts into the fixture directory")
    prod_eval_nested = production_sub.add_parser("semantic-eval", help="Run the baseline semantic eval harness against a fixture")
    prod_eval_nested.add_argument("--fixture", type=Path, required=True)
    prod_eval_nested.add_argument("--case-set", default="baseline")
    prod_eval_nested.add_argument("--out", type=Path, help="Write semantic eval result JSON")
    prod_prod_eval_nested = production_sub.add_parser("eval", help="Run read-only production semantic eval for curated central memory")
    prod_prod_eval_nested.add_argument("--job-id", default=DEFAULT_TARGET_JOB_ID)
    prod_prod_eval_nested.add_argument("--repo-id", default=DEFAULT_TARGET_REPO_ID)
    prod_prod_eval_nested.add_argument("--mode", default="baseline", choices=["baseline", "pre_apply", "post_apply"])
    prod_prod_eval_nested.add_argument("--out", type=Path, help="Write production semantic eval JSON")
    prod_plan_nested = production_sub.add_parser("merge-plan", help="Show the latest central_version_merge plan for a production job")
    prod_plan_nested.add_argument("--job-id", required=True)
    prod_plan_nested.add_argument("--backfill", action="store_true", help="Create a dry-run merge plan for an old completed job if missing")
    prod_plan_nested.add_argument("--forced-by", default="manual-backfill")
    prod_apply_nested = production_sub.add_parser("merge-apply", help="Apply exact central atoms for an accepted central merge plan")
    prod_apply_nested.add_argument("--plan-id", required=True)
    prod_apply_nested.add_argument("--branch", default="main")
    prod_apply_nested.add_argument("--view", default="active")


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
            daemon_status=RuntimeDaemonStatus(settings),
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
            daemon_status=RuntimeDaemonStatus(settings),
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


__all__ = ["PIPELINE_COMMANDS", "PRODUCTION_SUBCOMMANDS", "add_pipeline_subcommands", "handle_pipeline_command"]
