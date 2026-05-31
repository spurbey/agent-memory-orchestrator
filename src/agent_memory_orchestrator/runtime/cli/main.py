from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...graph.store import GraphBackendUnavailable
from ...llm.qwen import QwenUnavailable
from ...reasoning_graph.central_merge.production_eval import DEFAULT_TARGET_JOB_ID
from ...reasoning_graph.central_merge.production_eval import DEFAULT_TARGET_REPO_ID
from ..daemon.client import DaemonUnavailable
from .commands.bootstrap import handle_bootstrap_command as _handle_bootstrap_command
from .commands.debug import handle_debug_command as _handle_debug_command
from .commands.connectors import handle_connector_command as _handle_connector_command
from .commands.graph import _retrieve_index_only as _graph_retrieve_index_only
from .commands.graph import add_graph_subcommands as _add_graph_subcommands
from .commands.graph import handle_graph_command as _handle_graph_command
from .commands.install import add_model_selection_args as _add_model_selection_args
from .commands.install import handle_install_command as _handle_install_command
from .commands.memory import handle_memory_command as _handle_memory_command
from .commands.memory import rebuild_clean_db
from .commands.models import handle_models_command as _handle_models_command
from .commands.orchestration import handle_orchestration_command as _handle_orchestration_command
from .commands.pipeline import handle_pipeline_command as _handle_pipeline_command
from .commands.peer import add_peer_subcommands as _add_peer_subcommands
from .commands.peer import handle_peer_command as _handle_peer_command
from .commands.skill_checkpoint import add_skill_checkpoint_subcommands as _add_skill_checkpoint_subcommands
from .commands.skill_checkpoint import handle_skill_checkpoint_command as _handle_skill_checkpoint_command

_rebuild_clean_db = rebuild_clean_db
_retrieve_index_only = _graph_retrieve_index_only


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def _print_line(payload: object) -> None:
    print(json.dumps(payload), flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize local database schema")
    sub.add_parser("init-graph", help="Initialize local Kuzu GraphRAG schema")
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

    install = sub.add_parser("install", help="Configure Claude/Codex hooks, MCP, and local AMO runtime config")
    install.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    install.add_argument("--user-home", type=Path, default=Path.home(), help="Home directory containing .codex/.claude")
    install.add_argument(
        "--amo-home",
        type=Path,
        default=Path.home() / ".agent-memory-orchestrator",
        help="AMO data/config home used by hooks and MCP.",
    )
    _add_model_selection_args(install)
    install.add_argument(
        "--python-command",
        default=sys.executable or "python",
        help="Python executable visible to Claude/Codex hooks.",
    )
    install.add_argument("--download-models", action="store_true", help="Download selected local models during install.")
    install.add_argument("--skip-init-db", action="store_true", help="Do not initialize the AMO SQLite database.")
    install.add_argument("--dry-run", action="store_true", help="Show planned changes without writing files.")
    install.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")
    install.add_argument("--json", action="store_true", help="Print machine-readable install details.")
    install.add_argument("--force", action="store_true", help="Overwrite existing AMO target entries when safe.")

    doctor_cmd = sub.add_parser("doctor", help="Check AMO install/config status")
    doctor_cmd.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    doctor_cmd.add_argument("--user-home", type=Path, default=Path.home())
    doctor_cmd.add_argument("--amo-home", type=Path, default=Path.home() / ".agent-memory-orchestrator")

    uninstall_cmd = sub.add_parser("uninstall", help="Remove AMO-managed Claude/Codex config entries")
    uninstall_cmd.add_argument("--target", choices=["codex", "claude", "all"], default="all")
    uninstall_cmd.add_argument("--user-home", type=Path, default=Path.home())
    uninstall_cmd.add_argument("--yes", action="store_true", help="Apply without interactive confirmation.")

    ingest = sub.add_parser("ingest-transcript", help="Ingest JSONL transcript")
    ingest.add_argument("--agent", required=True, choices=["claude", "codex", "user", "system"])
    ingest.add_argument("--file", required=True, type=Path)
    ingest.add_argument("--session-id", required=True)
    ingest.add_argument("--session-title")

    hook = sub.add_parser("ingest-hook", help="Ingest one Claude/Codex hook JSON payload")
    hook.add_argument("--agent", default="codex", choices=["claude", "codex", "user", "system"])
    hook.add_argument("--file", required=True, type=Path)

    codex_import = sub.add_parser("import-codex-sessions", help="Import Codex rollout JSONL sessions")
    codex_import.add_argument("--root", type=Path, default=Path.home() / ".codex" / "sessions")
    codex_import.add_argument("--limit", type=int, default=30)
    codex_import.add_argument("--defer-vectors", action="store_true", help="Skip embeddings during import; run rebuild-indexes later.")
    codex_import.add_argument(
        "--include-existing",
        action="store_true",
        help="Reprocess sessions that already have imported events. Default skips them to avoid duplicates.",
    )

    clean = sub.add_parser("rebuild-clean-db", help="Create a fresh DB from raw Codex sessions")
    clean.add_argument("--out", required=True, type=Path)
    clean.add_argument("--codex-root", type=Path, default=Path.home() / ".codex" / "sessions")
    clean.add_argument("--limit", type=int, default=30)
    clean.add_argument("--force", action="store_true")

    sub.add_parser("print-codex-hooks", help="Print a Codex config.toml snippet for AMO capture-only hooks")

    search = sub.add_parser("search", help="Search memories")
    search.add_argument("--query", required=True)
    search.add_argument("--session-id")
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--include-historical", action="store_true")

    context = sub.add_parser("context-pack", help="Build an agent-ready memory context pack")
    context.add_argument("--query", required=True)
    context.add_argument("--session-id")
    context.add_argument("--budget", type=int, default=None)
    context.add_argument("--limit", type=int, default=12)
    context.add_argument("--include-historical", action="store_true")
    context.add_argument("--format", choices=["json", "text"], default="json")

    _add_graph_subcommands(sub)

    slack = sub.add_parser("slack", help="Configure and run local Slack Socket Mode connector")
    slack_sub = slack.add_subparsers(dest="slack_command", required=True)
    slack_manifest = slack_sub.add_parser("manifest", help="Print or write a Slack app manifest for Socket Mode")
    slack_manifest.add_argument("--out", type=Path, help="Optional output path for manifest JSON")
    slack_manifest.add_argument("--app-name", default="Agent Memory Orchestrator")
    slack_setup_link = slack_sub.add_parser("setup-link", help="Print a one-click Slack app creation URL with manifest prefilled")
    slack_setup_link.add_argument("--app-name", default="Agent Memory Orchestrator")
    slack_bootstrap = slack_sub.add_parser("bootstrap", help="Create the Slack app through the Manifest API using a config token")
    slack_bootstrap.add_argument("--config-token", required=True, help="Temporary Slack app configuration token, usually xoxe...")
    slack_bootstrap.add_argument("--team-id", default="", help="Optional Slack team id for org tokens")
    slack_bootstrap.add_argument("--app-name", default="Agent Memory Orchestrator")
    slack_setup = slack_sub.add_parser("setup", help="Write local Slack connector config")
    slack_setup.add_argument("--team-id", default="")
    slack_setup.add_argument("--bot-user-id", default="")
    slack_setup.add_argument("--capture-user-id", action="append", default=[])
    slack_setup.add_argument("--allowed-channel", action="append", default=[])
    slack_setup.add_argument("--session-idle-minutes", type=int, default=30)
    slack_setup.add_argument("--app-token", default="")
    slack_setup.add_argument("--bot-token", default="")
    slack_setup.add_argument("--save-tokens", action="store_true", help="Store tokens under AMO_HOME/.secrets/slack.json")
    slack_setup.add_argument("--skip-token-validation", action="store_true", help="Validate token shape only; do not call Slack API")
    slack_wizard = slack_sub.add_parser("setup-wizard", help="Interactively paste Slack tokens and write local config")
    slack_wizard.add_argument("--skip-token-validation", action="store_true", help="Validate token shape only; do not call Slack API")
    slack_wizard.add_argument("--no-save-tokens", action="store_true", help="Do not save tokens locally by default")
    slack_sub.add_parser("status", help="Show local Slack connector config without printing token values")
    slack_ingest = slack_sub.add_parser("ingest-event", help="Ingest one saved Slack Socket Mode event JSON file")
    slack_ingest.add_argument("--file", required=True, type=Path)
    slack_finalize = slack_sub.add_parser("finalize-session", help="Append a connector finalize event for graph-drain")
    slack_finalize.add_argument("--session-id", required=True)
    slack_finalize.add_argument("--reason", default="idle_timeout")
    slack_finalize.add_argument("--message-count", type=int, default=0)
    slack_run = slack_sub.add_parser("run", help="Run the local outbound Slack Socket Mode connector")
    slack_run.add_argument("--reply-mode", choices=["disabled", "ack", "answer"], default="answer")

    _add_peer_subcommands(sub)

    debug = sub.add_parser("debug", help="Debug AMO hook, drain, Qwen, graph, and retrieval stages")
    debug_sub = debug.add_subparsers(dest="debug_command", required=True)
    debug_sub.add_parser("hooks", help="Check hook config, log, and latest evidence")
    debug_drain = debug_sub.add_parser("drain", help="Show pending drain cursor/evidence state")
    debug_drain.add_argument("--session-id", default="")
    debug_qwen_cmd = debug_sub.add_parser("qwen", help="Check Qwen availability and query-planner JSON")
    debug_qwen_cmd.add_argument("--sample", default="what did we decide about codex hooks")
    debug_graph_cmd = debug_sub.add_parser("graph", help="Show graph status and current context")
    debug_graph_cmd.add_argument("--session-id", default="")
    debug_retrieval = debug_sub.add_parser("retrieval", help="Show retrieval output through daemon")
    debug_retrieval.add_argument("--query", required=True)
    debug_retrieval.add_argument("--limit", type=int, default=8)

    _add_skill_checkpoint_subcommands(sub)

    timeline = sub.add_parser("timeline", help="View session timeline")
    timeline.add_argument("--session-id", required=True)
    timeline.add_argument("--limit", type=int, default=50)

    export_cmd = sub.add_parser("export", help="Export snapshot to JSONL")
    export_cmd.add_argument("--out", required=True, type=Path)
    export_cmd.add_argument("--session-id")

    import_cmd = sub.add_parser("import", help="Import snapshot JSONL")
    import_cmd.add_argument("--file", required=True, type=Path)

    summary = sub.add_parser("session-summary", help="Generate deterministic session summary")
    summary.add_argument("--session-id", required=True)

    sub.add_parser("metrics", help="Inspect pipeline/retrieval row counts and latest retrieval")
    rebuild = sub.add_parser("rebuild-indexes", help="Rebuild FTS/vector index rows from canonical memory_units")
    rebuild.add_argument("--force-vectors", action="store_true")

    models = sub.add_parser("models", help="Manage local embedding/reranker models")
    model_sub = models.add_subparsers(dest="models_command", required=True)
    model_sub.add_parser("list", help="List hardware-aware model presets")
    model_status_cmd = model_sub.add_parser("status", help="Check whether selected models are cached locally")
    _add_model_selection_args(model_status_cmd)
    model_status_cmd.add_argument("--load-check", action="store_true", help="Also try loading models with local_files_only")
    model_download = model_sub.add_parser("download", help="Intentionally download/cache selected models once")
    _add_model_selection_args(model_download)
    model_download.add_argument("--cache-dir", type=Path)
    model_preflight = model_sub.add_parser("preflight", help="Require selected models to load from local cache")
    _add_model_selection_args(model_preflight)

    orch_start = sub.add_parser("orchestrate-start", help="Start orchestrator session")
    orch_start.add_argument("--session-id", required=True)
    orch_start.add_argument("--title")

    orch_submit = sub.add_parser("orchestrate-submit", help="Submit orchestrator round")
    orch_submit.add_argument("--session-id", required=True)
    orch_submit.add_argument("--agent", required=True, choices=["claude", "codex"])
    orch_submit.add_argument("--summary", required=True)
    orch_submit.add_argument("--confidence", required=True, type=float)
    orch_submit.add_argument("--artifact-uri", default="")
    orch_submit.add_argument("--blocking-issue", action="append", default=[])

    orch_status = sub.add_parser("orchestrate-status", help="Get orchestrator status")
    orch_status.add_argument("--session-id", required=True)

    orch_decide = sub.add_parser("orchestrate-decision", help="Apply user decision")
    orch_decide.add_argument("--session-id", required=True)
    orch_decide.add_argument("--decision", required=True, choices=["approved", "rejected"])
    orch_decide.add_argument("--notes", default="")
    orch_decide.add_argument("--decided-by", default="user")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        install_status = _handle_install_command(args, emit=_print, emit_text=print)
        if install_status is not None:
            return install_status

        bootstrap_status = _handle_bootstrap_command(args, emit=_print)
        if bootstrap_status is not None:
            return bootstrap_status

        pipeline_status = _handle_pipeline_command(args, emit=_print)
        if pipeline_status is not None:
            return pipeline_status

        models_status = _handle_models_command(args, emit=_print)
        if models_status is not None:
            return models_status

        connector_status = _handle_connector_command(args, emit=_print)
        if connector_status is not None:
            return connector_status

        peer_status = _handle_peer_command(args, emit=_print, emit_line=_print_line)
        if peer_status is not None:
            return peer_status

        memory_status = _handle_memory_command(args, emit=_print, emit_text=print)
        if memory_status is not None:
            return memory_status

        graph_status = _handle_graph_command(args, emit=_print)
        if graph_status is not None:
            return graph_status

        debug_status = _handle_debug_command(args, emit=_print)
        if debug_status is not None:
            return debug_status

        skill_checkpoint_status = _handle_skill_checkpoint_command(args, emit=_print)
        if skill_checkpoint_status is not None:
            return skill_checkpoint_status

        orchestration_status = _handle_orchestration_command(args, emit=_print)
        if orchestration_status is not None:
            return orchestration_status

        parser.error(f"unknown command: {args.command}")
        return 2
    except (DaemonUnavailable, GraphBackendUnavailable, QwenUnavailable) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
