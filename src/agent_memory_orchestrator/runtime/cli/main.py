from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ...graph.store import GraphBackendUnavailable
from ...llm.qwen import QwenUnavailable
from ...reasoning_graph.session_runtime import DEFAULT_CODE_EMBEDDING_MODEL
from ...reasoning_graph.central_merge.production_eval import DEFAULT_TARGET_JOB_ID
from ...reasoning_graph.central_merge.production_eval import DEFAULT_TARGET_REPO_ID
from ..daemon.client import DaemonUnavailable
from .commands.bootstrap import handle_bootstrap_command as _handle_bootstrap_command
from .commands.debug import handle_debug_command as _handle_debug_command
from .commands.connectors import handle_connector_command as _handle_connector_command
from .commands.graph import _retrieve_index_only as _graph_retrieve_index_only
from .commands.graph import handle_graph_command as _handle_graph_command
from .commands.install import add_model_selection_args as _add_model_selection_args
from .commands.install import handle_install_command as _handle_install_command
from .commands.memory import handle_memory_command as _handle_memory_command
from .commands.memory import rebuild_clean_db
from .commands.models import handle_models_command as _handle_models_command
from .commands.orchestration import handle_orchestration_command as _handle_orchestration_command
from .commands.pipeline import handle_pipeline_command as _handle_pipeline_command
from .commands.peer import add_peer_netd_start_args as _add_peer_netd_start_args
from .commands.peer import add_peer_netd_watch_service_args as _add_peer_netd_watch_service_args
from .commands.peer import handle_peer_command as _handle_peer_command
from .commands.skill_checkpoint import DEFAULT_LOCAL_NUM_CTX
from .commands.skill_checkpoint import DEFAULT_NUM_PREDICT
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

    graph_search = sub.add_parser("graph-search", help="Explicit Kuzu GraphRAG search")
    graph_search.add_argument("--query", required=True)
    graph_search.add_argument("--limit", type=int, default=8)
    graph_search.add_argument("--include-raw", action="store_true")
    graph_search.add_argument("--include-historical", action="store_true")
    graph_search.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_status = sub.add_parser("graph-status", help="Inspect Kuzu graph merge status")
    graph_status.add_argument("--session-id", default="")
    graph_status.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_drain = sub.add_parser("graph-drain", help="Daemon drains captured evidence into the Kuzu session graph")
    graph_drain.add_argument("--session-id", default="")
    graph_drain.add_argument("--limit", type=int, default=500)
    graph_drain.add_argument("--max-windows", type=int, default=None, help="Maximum Qwen trigger windows to process in one request.")
    graph_drain.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieval_build = sub.add_parser("graph-retrieval-build", help="Build SQLite/FTS retrieval docs from the graph")
    graph_retrieval_build.add_argument("--session-id", default="")
    graph_retrieval_build.add_argument("--repo-id", default="", help="Limit retrieval docs to one canonical repo id.")
    graph_retrieval_build.add_argument("--limit", type=int, default=10000)
    graph_retrieval_build.add_argument("--max-doc-chars", type=int, default=5000)
    graph_retrieval_build.add_argument("--db-path", type=Path, default=None)
    graph_retrieval_build.add_argument("--graph-path", type=Path, default=None)
    graph_retrieval_build.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieval_embed = sub.add_parser("graph-retrieval-embed", help="Resume embedding missing graph retrieval docs")
    graph_retrieval_embed.add_argument("--session-id", default="")
    graph_retrieval_embed.add_argument("--repo-id", default="", help="Limit embedding work to one canonical repo id.")
    graph_retrieval_embed.add_argument("--limit", type=int, default=100)
    graph_retrieval_embed.add_argument("--model", default="")
    graph_retrieval_embed.add_argument("--graph-scope", default="")
    graph_retrieval_embed.add_argument("--db-path", type=Path, default=None)
    graph_retrieval_embed.add_argument("--graph-path", type=Path, default=None)
    graph_retrieval_embed.add_argument("--no-faiss", action="store_true", help="Do not rebuild the FAISS cache after embedding.")
    graph_retrieval_embed.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieve = sub.add_parser("graph-retrieve", help="Retrieve over graph docs with exact/BM25/vector/Kuzu expansion")
    graph_retrieve.add_argument("--query", required=True)
    graph_retrieve.add_argument("--session-id", default="")
    graph_retrieve.add_argument("--repo-id", default="", help="Search one canonical repo id.")
    graph_retrieve.add_argument("--limit", type=int, default=8)
    graph_retrieve.add_argument("--model", default="")
    graph_retrieve.add_argument("--graph-scope", default="")
    graph_retrieve.add_argument("--db-path", type=Path, default=None)
    graph_retrieve.add_argument("--graph-path", type=Path, default=None)
    graph_retrieve.add_argument("--no-vector", action="store_true")
    graph_retrieve.add_argument("--require-vector", action="store_true", help="Fail instead of falling back if vector retrieval returns no candidates.")
    graph_retrieve.add_argument("--no-answer", action="store_true")
    graph_retrieve.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_version_flow = sub.add_parser("graph-version-flow", help="Show commit-centric graph versioning flow")
    graph_version_flow.add_argument("--commit", default="", help="Commit SHA/prefix to inspect. Omit to list recent flows.")
    graph_version_flow.add_argument("--session-id", default="", help="Restrict version flow to one AMO session.")
    graph_version_flow.add_argument("--repo-id", default="", help="Limit version flow to one canonical repo id.")
    graph_version_flow.add_argument("--limit", type=int, default=100)
    graph_version_flow.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_build_session = sub.add_parser(
        "graph-build-session",
        help="Build an isolated production session graph from real AMO evidence and Codex transcripts",
    )
    graph_build_session.add_argument("--session-id", required=True)
    graph_build_session.add_argument("--commit", required=True)
    graph_build_session.add_argument("--repo-root", type=Path, default=Path.cwd())
    graph_build_session.add_argument("--graph-path", type=Path, default=None)
    graph_build_session.add_argument("--evidence-path", action="append", type=Path, default=[])
    graph_build_session.add_argument("--transcript-path", action="append", type=Path, default=[])
    graph_build_session.add_argument("--file-path", action="append", default=[])
    graph_build_session.add_argument("--query", default="")
    graph_build_session.add_argument("--code-query", default="")
    graph_build_session.add_argument("--limit", type=int, default=8)
    graph_build_session.add_argument("--limit-events", type=int, default=None)
    graph_build_session.add_argument("--force", action="store_true", help="Replace the target session graph path.")
    graph_build_session.add_argument("--text-embedding-model", default="")
    graph_build_session.add_argument("--code-embedding-model", default=DEFAULT_CODE_EMBEDDING_MODEL)

    graph_session_search = sub.add_parser("graph-session-search", help="Search an isolated production session graph")
    graph_session_search.add_argument("--graph-path", required=True, type=Path)
    graph_session_search.add_argument("--query", default="")
    graph_session_search.add_argument("--code-query", default="")
    graph_session_search.add_argument("--limit", type=int, default=8)
    graph_session_search.add_argument("--text-embedding-model", default="")
    graph_session_search.add_argument("--code-embedding-model", default=DEFAULT_CODE_EMBEDDING_MODEL)

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

    peer = sub.add_parser("peer", help="Configure AMO peer rooms and the local libp2p sidecar")
    peer.add_argument("--amo-home", type=Path, help="AMO home directory containing peer config and room state.")
    peer_sub = peer.add_subparsers(dest="peer_command", required=True)
    peer_doctor_cmd = peer_sub.add_parser("doctor", help="Check peer identity, netd source, binary, sidecar, and peers")
    peer_doctor_cmd.add_argument("--strict", action="store_true", help="Return non-zero unless peer rooms are ready now.")
    peer_init = peer_sub.add_parser("init", help="Initialize this AMO node's peer identity")
    peer_init.add_argument("--node-id", required=True)
    peer_init.add_argument("--display-name", default="")
    peer_init.add_argument("--capability", action="append", default=[])
    peer_add = peer_sub.add_parser("add", help="Add a trusted peer identity and optional transport addresses")
    peer_add.add_argument("--node-id", required=True)
    peer_add.add_argument("--base-url", default="", help="Legacy direct HTTP URL, e.g. http://100.76.18.75:8787")
    peer_add.add_argument("--peer-id", default="", help="libp2p peer id for amo-peer-netd delivery.")
    peer_add.add_argument("--multiaddr", action="append", default=[], help="Dialable libp2p multiaddr. Repeat as needed.")
    peer_add.add_argument("--relay-addr", action="append", default=[], help="Dialable relay /p2p-circuit multiaddr. Repeat as needed.")
    peer_add.add_argument("--rendezvous-addr", default="", help="Rendezvous node multiaddr used for discovery.")
    peer_add.add_argument("--rendezvous-namespace", default="", help="Rendezvous namespace for this peer/group.")
    peer_add.add_argument("--display-name", default="")
    peer_add.add_argument("--capability", action="append", default=[])
    peer_add.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="trusted")
    peer_add.add_argument(
        "--shared-secret-env",
        default="",
        help="Optional environment variable containing this peer's HMAC shared secret.",
    )
    peer_remove = peer_sub.add_parser("remove", help="Remove a configured peer identity")
    peer_remove.add_argument("--node-id", required=True)
    peer_sub.add_parser("status", help="Show peer node, policy, configured peers, and room count")
    peer_share = peer_sub.add_parser("share-card", help="Print or write this node's importable peer card")
    peer_share.add_argument("--out", type=Path, help="Optional JSON output path.")
    peer_share.add_argument("--base-url", default="", help="Optional legacy direct HTTP URL to include.")
    peer_share.add_argument("--rendezvous-addr", default="", help="Optional rendezvous node multiaddr to include.")
    peer_share.add_argument("--rendezvous-namespace", default="", help="Optional rendezvous namespace to include.")
    peer_share.add_argument("--relay-profile", "--relay", dest="relay_profile", default="", help="Saved relay profile to include in this card.")
    peer_import = peer_sub.add_parser("import-card", help="Import a trusted peer from a peer-card JSON file")
    peer_import.add_argument("--file", required=True, type=Path)
    peer_import.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="trusted")
    peer_import.add_argument("--shared-secret-env", default="")
    peer_invite = peer_sub.add_parser("create-invite", help="Create a shareable peer invite bundle/code")
    peer_invite.add_argument("--out", type=Path, help="Optional JSON invite output path.")
    peer_invite.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="trusted")
    peer_invite.add_argument("--shared-secret-env", default="")
    peer_invite.add_argument("--label", default="", help="Optional human-readable invite label.")
    peer_invite.add_argument("--base-url", default="", help="Optional legacy direct HTTP URL to include.")
    peer_invite.add_argument("--rendezvous-addr", default="", help="Optional rendezvous node multiaddr to include.")
    peer_invite.add_argument("--rendezvous-namespace", default="", help="Optional rendezvous namespace to include.")
    peer_invite.add_argument("--relay-profile", "--relay", dest="relay_profile", default="", help="Saved relay profile to include in this invite.")
    peer_invite.add_argument("--auto-approve", action="store_true", help="Auto-import the accepting peer after token proof.")
    peer_invite.add_argument("--expires-minutes", type=int, default=1440, help="Invite validity window.")
    peer_invite.add_argument("--max-uses", type=int, default=1, help="Maximum accepted join requests.")
    peer_accept = peer_sub.add_parser("accept-invite", help="Import an invite and optionally write this node's response card")
    peer_accept_source = peer_accept.add_mutually_exclusive_group(required=True)
    peer_accept_source.add_argument("--file", type=Path, help="Invite JSON file to accept.")
    peer_accept_source.add_argument("--code", default="", help="amo-peer-invite: code to accept.")
    peer_accept.add_argument("--trust", choices=["trusted", "limited", "blocked"], default="")
    peer_accept.add_argument("--shared-secret-env", default="")
    peer_accept.add_argument("--response-out", type=Path, help="Optional response peer-card JSON path to send back.")
    peer_accept.add_argument("--no-send-join-request", action="store_true", help="Do not send the automatic return join request.")
    peer_join_requests = peer_sub.add_parser("join-requests", help="List pending peer join requests")
    peer_join_requests.add_argument("--status", default="", choices=["", "pending", "approved", "rejected"])
    peer_approve_join = peer_sub.add_parser("approve-join", help="Approve a pending peer join request")
    peer_approve_join.add_argument("--request-id", required=True)
    peer_reject_join = peer_sub.add_parser("reject-join", help="Reject a pending peer join request")
    peer_reject_join.add_argument("--request-id", required=True)
    peer_reject_join.add_argument("--reason", default="")
    peer_sub.add_parser("rooms", help="List local peer investigation rooms")
    peer_context = peer_sub.add_parser("context", help="Build the three-layer context pack for a room")
    peer_context.add_argument("--room-id", required=True)
    peer_context.add_argument("--viewer-node-id", default="", help="Defaults to this AMO node id")
    peer_message = peer_sub.add_parser("append-message", help="Append a local peer-room message for smoke tests/manual use")
    peer_message.add_argument("--room-id", required=True)
    peer_message.add_argument("--from-node-id", required=True)
    peer_message.add_argument("--to-node-id", action="append", default=[])
    peer_message.add_argument("--type", default="context_request")
    peer_message.add_argument("--content", required=True)
    peer_message.add_argument("--citation", action="append", default=[])
    peer_message.add_argument("--confidence", type=float)
    peer_send = peer_sub.add_parser("send-message", help="Append and send a room message through amo-peer-netd")
    peer_send.add_argument("--room-id", required=True)
    peer_send.add_argument("--peer-id", required=True, help="Configured AMO peer node id, not the libp2p peer id.")
    peer_send.add_argument("--type", default="context_request")
    peer_send.add_argument("--content", required=True)
    peer_send.add_argument("--citation", action="append", default=[])
    peer_send.add_argument("--confidence", type=float)
    peer_summary = peer_sub.add_parser("update-summary", help="Replace a room's initiator-owned rolling summary")
    peer_summary.add_argument("--room-id", required=True)
    peer_summary.add_argument("--summary", required=True)
    peer_room = peer_sub.add_parser("open-room", help="Create an investigation room and invite configured peers")
    peer_room.add_argument("--topic", required=True)
    peer_room.add_argument("--peer", action="append", default=[], help="Peer node id to invite. Repeat for multiple peers.")
    peer_room.add_argument("--no-send", action="store_true", help="Create the room locally without sending invites.")
    peer_setup = peer_sub.add_parser("setup", help="One-time peer setup: init identity, start relay sidecar, and optionally install startup")
    _add_peer_netd_start_args(peer_setup)
    peer_setup.add_argument("--display-name", default="", help="Display name to save for this AMO peer identity.")
    peer_setup.add_argument("--capability", action="append", default=[], help="Capability to save on first setup. Repeat as needed.")
    peer_setup.add_argument("--relay-addr", default="", help="Relay multiaddr to save before starting, e.g. from the AWS relay output.")
    peer_setup.add_argument("--namespace", default="", help="Rendezvous namespace to save with --relay-addr.")
    peer_setup.add_argument("--profile-name", default="", help="Profile name to save when --relay-addr is provided. Defaults to --relay/--relay-profile or default.")
    peer_setup_invite = peer_setup.add_mutually_exclusive_group()
    peer_setup_invite.add_argument("--invite", type=Path, help="Invite JSON to accept after relay startup.")
    peer_setup_invite.add_argument("--invite-code", default="", help="amo-peer-invite: code to accept after relay startup.")
    peer_setup.add_argument("--install-startup", action="store_true", help="Install OS startup entries for sidecar and peer-agent watch.")
    peer_setup.add_argument("--service-name", default="AMO Peer Netd")
    _add_peer_netd_watch_service_args(peer_setup)
    peer_setup.add_argument("--no-start", action="store_true", help="Only save config/profile; do not start peer-netd now.")
    peer_enable = peer_sub.add_parser("enable", help="Build if needed and start the managed libp2p sidecar")
    _add_peer_netd_start_args(peer_enable)
    peer_netd = peer_sub.add_parser("netd", help="Build, start, stop, and inspect the managed libp2p sidecar")
    peer_netd_sub = peer_netd.add_subparsers(dest="netd_command", required=True)
    peer_netd_build = peer_netd_sub.add_parser("build", help="Compile amo-peer-netd into AMO_HOME/.peer/bin")
    peer_netd_build.add_argument("--out", type=Path, help="Optional output binary path.")
    peer_netd_start = peer_netd_sub.add_parser("start", help="Start the managed libp2p sidecar")
    _add_peer_netd_start_args(peer_netd_start)
    peer_netd_sub.add_parser("stop", help="Stop the managed libp2p sidecar")
    peer_netd_sub.add_parser("status", help="Show managed libp2p sidecar process and health state")
    peer_netd_install = peer_netd_sub.add_parser("install-service", help="Plan or install OS startup for peer netd")
    _add_peer_netd_start_args(peer_netd_install)
    peer_netd_install.add_argument("--service-name", default="AMO Peer Netd")
    _add_peer_netd_watch_service_args(peer_netd_install)
    peer_netd_install.add_argument("--apply", action="store_true", help="Actually create the OS startup entry.")
    peer_netd_uninstall = peer_netd_sub.add_parser("uninstall-service", help="Plan or remove OS startup for peer netd")
    peer_netd_uninstall.add_argument("--service-name", default="AMO Peer Netd")
    _add_peer_netd_watch_service_args(peer_netd_uninstall)
    peer_netd_uninstall.add_argument("--apply", action="store_true", help="Actually remove the OS startup entry.")
    peer_netd_service_status_cmd = peer_netd_sub.add_parser("service-status", help="Inspect the OS startup entry for peer netd")
    peer_netd_service_status_cmd.add_argument("--service-name", default="AMO Peer Netd")
    _add_peer_netd_watch_service_args(peer_netd_service_status_cmd)
    peer_relay = peer_sub.add_parser("relay", help="Run or inspect a public AMO relay+rendezvous helper node")
    peer_relay_sub = peer_relay.add_subparsers(dest="relay_command", required=True)
    peer_relay_start = peer_relay_sub.add_parser("start", help="Start a combined circuit relay and rendezvous node")
    peer_relay_start.add_argument("--node-id", default="amo-relay")
    peer_relay_start.add_argument("--listen", default="/ip4/0.0.0.0/tcp/4001")
    peer_relay_start.add_argument("--api", default="127.0.0.1:8798")
    peer_relay_start.add_argument("--advertise-addr", action="append", default=[], help="Public libp2p multiaddr, e.g. /ip4/1.2.3.4/tcp/4001")
    peer_relay_start.add_argument("--namespace", default="amo-peer-default", help="Suggested rendezvous namespace for this trust group.")
    peer_relay_start.add_argument("--store-path", default="")
    peer_relay_start.add_argument("--no-build", action="store_true")
    peer_relay_save = peer_relay_sub.add_parser("save", help="Save a client relay profile for short --relay commands")
    peer_relay_save.add_argument("--name", required=True)
    peer_relay_save.add_argument("--addr", required=True, help="Relay/rendezvous multiaddr.")
    peer_relay_save.add_argument("--rendezvous-addr", default="", help="Optional distinct rendezvous multiaddr. Defaults to --addr.")
    peer_relay_save.add_argument("--namespace", required=True, help="Rendezvous namespace for this trust group.")
    peer_relay_save.add_argument("--no-auto-relay", action="store_true", help="Do not enable AutoRelay when this profile is used.")
    peer_relay_save.add_argument("--no-hole-punching", action="store_true", help="Do not enable hole punching when this profile is used.")
    peer_relay_show = peer_relay_sub.add_parser("show", help="Show one saved client relay profile")
    peer_relay_show.add_argument("--name", required=True)
    peer_relay_delete = peer_relay_sub.add_parser("delete", help="Delete one saved client relay profile")
    peer_relay_delete.add_argument("--name", required=True)
    peer_relay_sub.add_parser("list", help="List saved client relay profiles")
    peer_relay_sub.add_parser("status", help="Show the managed relay/rendezvous node status")
    peer_poll_netd = peer_sub.add_parser("poll-netd", help="Process delivered sidecar messages into local peer rooms")
    peer_poll_netd.add_argument("--limit", type=int, default=None)
    peer_poll_netd.add_argument("--watch", action="store_true", help="Keep polling the sidecar inbox until interrupted.")
    peer_poll_netd.add_argument("--interval-seconds", type=float, default=2.0)
    peer_poll_netd.add_argument("--max-iterations", type=int, default=0, help="Testing/debug guard for --watch. 0 means forever.")
    peer_poll_netd.add_argument("--fail-fast", action="store_true", help="In watch mode, exit on the first poll error.")
    peer_serve = peer_sub.add_parser("serve", help="Run the direct peer listener for Tailscale/private networking")
    peer_serve.add_argument("--host", default="0.0.0.0")
    peer_serve.add_argument("--port", type=int, default=8787)

    peer_agent = sub.add_parser("peer-agent", help="Run AMO peer-agent ask/watch/finalize workflows")
    peer_agent.add_argument("--amo-home", type=Path, help="AMO home directory for peer-agent state.")
    peer_agent_sub = peer_agent.add_subparsers(dest="peer_agent_command", required=True)
    peer_agent_ask = peer_agent_sub.add_parser("ask", help="Ask local memory first, then open a peer room if needed")
    peer_agent_ask.add_argument("--query", required=True)
    peer_agent_ask.add_argument("--peer", action="append", default=[], help="Trusted peer node id to ask. Repeat for multiple peers.")
    peer_agent_ask.add_argument("--session-id", default="")
    peer_agent_ask.add_argument("--min-confidence", type=float, default=None)
    peer_agent_ask.add_argument("--timeout-seconds", type=float, default=None)
    peer_agent_watch = peer_agent_sub.add_parser("watch", help="Drain peer inbox and respond/finalize rooms")
    peer_agent_watch.add_argument("--interval-seconds", type=float, default=2.0)
    peer_agent_watch.add_argument("--max-iterations", type=int, default=0, help="Testing/debug guard. 0 means forever.")
    peer_agent_watch.add_argument("--limit", type=int, default=None, help="Maximum netd envelopes to drain per tick.")
    peer_agent_watch.add_argument("--fail-fast", action="store_true")
    peer_agent_status = peer_agent_sub.add_parser("status", help="Show peer-agent room state")
    peer_agent_status.add_argument("--room-id", required=True)
    peer_agent_context = peer_agent_sub.add_parser("context", help="Show local peer-agent room context")
    peer_agent_context.add_argument("--room-id", required=True)
    peer_agent_messages = peer_agent_sub.add_parser("messages", help="Show peer-agent room messages")
    peer_agent_messages.add_argument("--room-id", required=True)
    peer_agent_summary = peer_agent_sub.add_parser("summarize", help="Update an initiator-owned room summary")
    peer_agent_summary.add_argument("--room-id", required=True)

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

    skill_checkpoint = sub.add_parser(
        "skill-checkpoint",
        help="Build or finalize a reusable skill from a compact checkpoint packet",
    )
    skill_checkpoint_sub = skill_checkpoint.add_subparsers(dest="skill_checkpoint_command", required=True)
    skill_checkpoint_extract = skill_checkpoint_sub.add_parser(
        "extract",
        help="Run local Ollama/Qwen over a compact checkpoint packet and write validated skill outputs",
    )
    skill_checkpoint_extract.add_argument("--packet", required=True, type=Path)
    skill_checkpoint_extract.add_argument("--out-dir", required=True, type=Path)
    skill_checkpoint_extract.add_argument("--amo-home", type=Path)
    skill_checkpoint_extract.add_argument("--num-ctx", type=int, default=DEFAULT_LOCAL_NUM_CTX)
    skill_checkpoint_extract.add_argument("--num-predict", type=int, default=DEFAULT_NUM_PREDICT)
    skill_checkpoint_extract.add_argument("--timeout-seconds", type=float)
    skill_checkpoint_extract.add_argument("--no-auto-repair-validation-refs", action="store_true")

    skill_checkpoint_mark = skill_checkpoint_sub.add_parser(
        "mark",
        help="Mark the current agent session as a pending skill checkpoint",
    )
    skill_checkpoint_mark.add_argument("--agent", choices=["codex", "claude"], default="codex")
    skill_checkpoint_mark.add_argument("--session-id", default="", help="Optional explicit session id. Defaults to latest captured session for the agent.")
    skill_checkpoint_mark.add_argument("--note", default="", help="Optional user intent or checkpoint note.")
    skill_checkpoint_mark.add_argument("--mode", choices=["workflow", "single_commit"], default="workflow")
    skill_checkpoint_mark.add_argument("--cwd", type=Path, default=Path.cwd())
    skill_checkpoint_mark.add_argument("--amo-home", type=Path)

    skill_checkpoint_status = skill_checkpoint_sub.add_parser(
        "status",
        help="List pending skill checkpoints",
    )
    skill_checkpoint_status.add_argument("--limit", type=int, default=20)
    skill_checkpoint_status.add_argument("--amo-home", type=Path)

    skill_checkpoint_finalize = skill_checkpoint_sub.add_parser(
        "finalize",
        help="Post-validate a Qwen skill-checkpoint result and render SKILL.md/provenance",
    )
    skill_checkpoint_finalize.add_argument("--result", required=True, type=Path)
    skill_checkpoint_finalize.add_argument("--packet", required=True, type=Path)
    skill_checkpoint_finalize.add_argument("--out-dir", required=True, type=Path)
    skill_checkpoint_finalize.add_argument("--amo-home", type=Path)
    skill_checkpoint_finalize.add_argument("--no-auto-repair-validation-refs", action="store_true")

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
