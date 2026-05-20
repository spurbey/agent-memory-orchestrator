from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..integrations.connectors.slack import SlackConnectorService
from ..integrations.connectors.slack.manifest import slack_manifest_json, slack_manifest_setup_url
from ..integrations.connectors.slack.service import load_event_file
from ..integrations.connectors.slack.socket_mode import SlackSocketModeRunner
from ..integrations.connectors.slack.wizard import run_slack_setup_wizard
from ..graph.diagnostics import debug_hooks, debug_qwen
from ..graph.service import GraphRagService
from ..graph.store import GraphBackendUnavailable
from ..install.service import InstallOptions
from ..install.service import apply_install_plan
from ..install.service import build_install_plan
from ..install.service import doctor as install_doctor
from ..install.service import uninstall as uninstall_targets
from ..memory import MemoryService
from ..orchestration import OrchestratorService
from ..core.privacy import redact_secrets
from ..peer import PeerService
from ..peer.doctor import peer_doctor
from ..peer.invites import decode_invite_code
from ..peer.netd_runtime import PeerNetdLaunchOptions
from ..peer.netd_runtime import PeerNetdRuntime
from ..peer.netd_service import PeerNetdServiceOptions
from ..peer.netd_service import install_service as install_peer_netd_service
from ..peer.netd_service import service_status as peer_netd_service_status
from ..peer.netd_service import uninstall_service as uninstall_peer_netd_service
from ..peer.server import main as peer_server_main
from ..llm.models import download_models, list_model_presets, model_status, preflight_models
from ..llm.qwen import QwenUnavailable
from ..reasoning_graph.session_runtime import DEFAULT_CODE_EMBEDDING_MODEL
from ..reasoning_graph.session_runtime import SessionGraphBuildOptions
from ..reasoning_graph.session_runtime import SessionGraphQueryOptions
from ..reasoning_graph.session_runtime import build_and_query_session_graph
from ..reasoning_graph.session_runtime import build_session_graph
from ..reasoning_graph.session_runtime import default_session_graph_path
from ..reasoning_graph.session_runtime import query_session_graph
from ..reasoning_graph.jobs.reset import reset_production_v2_storage
from ..skill_checkpoint import DEFAULT_LOCAL_NUM_CTX
from ..skill_checkpoint import DEFAULT_NUM_PREDICT
from ..skill_checkpoint import list_skill_checkpoints
from ..skill_checkpoint import mark_skill_checkpoint
from ..skill_checkpoint import run_local_skill_checkpoint_extraction
from ..skill_checkpoint import write_skill_checkpoint_outputs
from .client import DaemonClient, DaemonUnavailable


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def _print_line(payload: object) -> None:
    print(json.dumps(payload), flush=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Memory Orchestrator CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize local database schema")
    sub.add_parser("init-graph", help="Initialize local Kuzu GraphRAG schema")

    v2_reset = sub.add_parser("v2-reset-production", help="Explicitly back up and reset production V2 graph/retrieval stores")
    v2_reset.add_argument("--backup", action="store_true", help="Required. Create a timestamped backup before cleaning.")
    v2_reset.add_argument("--clean-graph", action="store_true", help="Clean/recreate the production Kuzu graph store.")
    v2_reset.add_argument("--clean-retrieval", action="store_true", help="Clean retrieval docs/vector ledger and FAISS cache.")
    v2_reset.add_argument(
        "--force-if-daemon-running",
        action="store_true",
        help="Allow reset even if the daemon health endpoint is reachable.",
    )

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

    graph_drain_smoke = sub.add_parser("graph-drain-smoke", help="Run legacy GraphDelta drain into a disposable smoke graph")
    graph_drain_smoke.add_argument("--limit", type=int, default=500)
    graph_drain_smoke.add_argument("--max-windows", type=int, default=None)
    graph_drain_smoke.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_cleanup = sub.add_parser("graph-cleanup-noisy", help="Find or abandon noisy draft graph answer nodes")
    graph_cleanup.add_argument("--limit", type=int, default=500)
    graph_cleanup.add_argument("--apply", action="store_true", help="Mark noisy nodes abandoned.")
    graph_cleanup.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_consolidate = sub.add_parser("graph-consolidate", help="Classify duplicate/refine/supersede/contradict graph edges")
    graph_consolidate.add_argument("--limit", type=int, default=500)
    graph_consolidate.add_argument("--apply", action="store_true", help="Write consolidation edges and topic cluster nodes.")
    graph_consolidate.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_cache_status = sub.add_parser("graph-cache-status", help="Inspect derived GraphRAG retrieval cache status")
    graph_cache_status.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_rebuild_cache = sub.add_parser("graph-rebuild-cache", help="Rebuild derived GraphRAG retrieval cache")
    graph_rebuild_cache.add_argument("--limit", type=int, default=5000)
    graph_rebuild_cache.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieval_build = sub.add_parser("graph-retrieval-build", help="Build SQLite/FTS retrieval docs from the graph")
    graph_retrieval_build.add_argument("--session-id", default="")
    graph_retrieval_build.add_argument("--limit", type=int, default=10000)
    graph_retrieval_build.add_argument("--max-doc-chars", type=int, default=5000)
    graph_retrieval_build.add_argument("--db-path", type=Path, default=None)
    graph_retrieval_build.add_argument("--graph-path", type=Path, default=None)
    graph_retrieval_build.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_retrieval_embed = sub.add_parser("graph-retrieval-embed", help="Resume embedding missing graph retrieval docs")
    graph_retrieval_embed.add_argument("--session-id", default="")
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
    graph_retrieve.add_argument("--limit", type=int, default=8)
    graph_retrieve.add_argument("--model", default="")
    graph_retrieve.add_argument("--graph-scope", default="")
    graph_retrieve.add_argument("--db-path", type=Path, default=None)
    graph_retrieve.add_argument("--graph-path", type=Path, default=None)
    graph_retrieve.add_argument("--no-vector", action="store_true")
    graph_retrieve.add_argument("--require-vector", action="store_true", help="Fail instead of falling back if vector retrieval returns no candidates.")
    graph_retrieve.add_argument("--no-answer", action="store_true")
    graph_retrieve.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_finalize = sub.add_parser("graph-finalize-session", help="Promote one session's draft graph work into central graph")
    graph_finalize.add_argument("--session-id", required=True)
    graph_finalize.add_argument("--commit", default="HEAD")
    graph_finalize.add_argument("--cwd", default="")
    graph_finalize.add_argument("--limit", type=int, default=500)
    graph_finalize.add_argument("--apply", action="store_true", help="Apply the merge plan. Omit for dry-run preview.")
    graph_finalize.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_rebuild_central = sub.add_parser("graph-rebuild-central", help="Rebuild central graph from raw evidence")
    graph_rebuild_central.add_argument("--from-evidence", action="store_true", help="Replay raw evidence into a fresh graph")
    graph_rebuild_central.add_argument("--backup-current", action="store_true", help="Back up current active graph before swap")
    graph_rebuild_central.add_argument("--apply", action="store_true", help="Apply backup/rebuild/swap. Omit for dry-run preview.")
    graph_rebuild_central.add_argument("--limit", type=int, default=100000)
    graph_rebuild_central.add_argument("--max-windows", type=int, default=None)
    graph_rebuild_central.add_argument("--offline", action="store_true", help="Open Kuzu directly for single-process maintenance.")

    graph_version_flow = sub.add_parser("graph-version-flow", help="Show commit-centric graph versioning flow")
    graph_version_flow.add_argument("--commit", default="", help="Commit SHA/prefix to inspect. Omit to list recent flows.")
    graph_version_flow.add_argument("--session-id", default="", help="Restrict version flow to one AMO session.")
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
    peer_sub.add_parser("status", help="Show peer node, policy, configured peers, and room count")
    peer_share = peer_sub.add_parser("share-card", help="Print or write this node's importable peer card")
    peer_share.add_argument("--out", type=Path, help="Optional JSON output path.")
    peer_share.add_argument("--base-url", default="", help="Optional legacy direct HTTP URL to include.")
    peer_share.add_argument("--rendezvous-addr", default="", help="Optional rendezvous node multiaddr to include.")
    peer_share.add_argument("--rendezvous-namespace", default="", help="Optional rendezvous namespace to include.")
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
        if args.command == "install":
            options = InstallOptions(
                target=args.target,
                user_home=args.user_home,
                amo_home=args.amo_home,
                preset=args.preset,
                embedding_model=args.embedding_model,
                reranker_model=args.reranker_model,
                qwen_model=args.qwen_model,
                python_command=args.python_command,
                force=args.force,
            )
            plan = build_install_plan(options)
            summary = _summarize_install_plan(plan)
            if args.dry_run:
                _print({"ok": True, "dry_run": True, "plan": summary})
                return 0
            if not args.yes:
                _print({"ok": True, "pending_plan": summary})
                if not _confirm("Apply AMO install changes?"):
                    _print({"ok": False, "cancelled": True, "plan": summary})
                    return 1
            result = apply_install_plan(plan)
            model_result = None
            if args.download_models:
                model_result = download_models(
                    preset=args.preset,
                    embedding_model=args.embedding_model,
                    reranker_model=args.reranker_model,
                    qwen_model=args.qwen_model,
                )
            init_result = None
            init_graph = None
            if not args.skip_init_db:
                os.environ["AMO_HOME"] = plan["amo_home"]
                init_settings = Settings.load()
                svc = MemoryService(init_settings)
                try:
                    svc.init_db()
                    init_result = {"db_path": str(init_settings.db_path)}
                finally:
                    svc.close()
                try:
                    graph = GraphRagService(init_settings)
                    graph.close()
                    init_graph = {"ok": True, "graph_path": str(init_settings.graph_path)}
                except GraphBackendUnavailable as exc:
                    init_graph = {"ok": False, "error": str(exc)}
            _print(
                {
                    "ok": True,
                    "plan": summary,
                    "apply": result,
                    "models": model_result,
                    "init_db": init_result,
                    "init_graph": init_graph,
                }
            )
            return 0

        if args.command == "doctor":
            result = install_doctor(target=args.target, user_home=args.user_home, amo_home=args.amo_home)
            _print(result)
            return 0 if result["ok"] else 1

        if args.command == "uninstall":
            if not args.yes and not _confirm("Remove AMO-managed config entries?"):
                _print({"ok": False, "cancelled": True})
                return 1
            _print(uninstall_targets(target=args.target, user_home=args.user_home))
            return 0

        if args.command == "init-db":
            settings = Settings.load()
            svc = MemoryService(settings)
            try:
                svc.init_db()
            finally:
                svc.close()
            _print({"ok": True, "db_path": str(settings.db_path)})
            return 0

        if args.command == "init-graph":
            settings = Settings.load()
            graph = GraphRagService(settings)
            try:
                _print({"ok": True, "graph_path": str(settings.graph_path), "backend": settings.graph_backend})
            finally:
                graph.close()
            return 0

        if args.command == "v2-reset-production":
            settings = Settings.load()
            result = reset_production_v2_storage(
                settings,
                backup=args.backup,
                clean_graph=args.clean_graph,
                clean_retrieval=args.clean_retrieval,
                force_if_daemon_running=args.force_if_daemon_running,
            )
            _print(result)
            return 0

        if args.command == "models":
            if args.models_command == "list":
                _print({"ok": True, "presets": list_model_presets()})
            elif args.models_command == "status":
                _print(
                    {
                        "ok": True,
                        "result": model_status(
                            preset=args.preset,
                            embedding_model=args.embedding_model,
                            reranker_model=args.reranker_model,
                            qwen_model=args.qwen_model,
                            load_check=args.load_check,
                        ),
                    }
                )
            elif args.models_command == "download":
                _print(
                    {
                        "ok": True,
                        "result": download_models(
                            preset=args.preset,
                            embedding_model=args.embedding_model,
                            reranker_model=args.reranker_model,
                            qwen_model=args.qwen_model,
                            cache_dir=args.cache_dir,
                        ),
                    }
                )
            elif args.models_command == "preflight":
                result = preflight_models(
                    preset=args.preset,
                    embedding_model=args.embedding_model,
                    reranker_model=args.reranker_model,
                    qwen_model=args.qwen_model,
                )
                _print({"ok": result["ok"], "result": result})
                return 0 if result["ok"] else 1
            return 0

        if args.command == "slack":
            if args.slack_command == "manifest":
                if args.out:
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(slack_manifest_json(app_name=args.app_name), encoding="utf-8")
                    _print({"ok": True, "path": str(args.out.resolve())})
                else:
                    print(slack_manifest_json(app_name=args.app_name), end="")
                return 0
            if args.slack_command == "setup-link":
                _print(
                    {
                        "ok": True,
                        "url": slack_manifest_setup_url(app_name=args.app_name),
                        "next_step": "Open this URL, select the workspace, review, and create the app.",
                    }
                )
                return 0
            settings = Settings.load()
            svc = SlackConnectorService(settings)
            if args.slack_command == "bootstrap":
                result = svc.bootstrap_with_config_token(
                    config_token=args.config_token,
                    team_id=args.team_id,
                    app_name=args.app_name,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.slack_command == "setup":
                result = svc.setup(
                    team_id=args.team_id,
                    bot_user_id=args.bot_user_id,
                    capture_user_ids=args.capture_user_id,
                    allowed_channels=args.allowed_channel,
                    session_idle_minutes=args.session_idle_minutes,
                    app_token=args.app_token,
                    bot_token=args.bot_token,
                    save_tokens=args.save_tokens,
                    skip_token_validation=args.skip_token_validation,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.slack_command == "setup-wizard":
                result = run_slack_setup_wizard(
                    svc,
                    default_save_tokens=not args.no_save_tokens,
                    default_validate_tokens=not args.skip_token_validation,
                )
                _print(result)
                return 0 if result.get("ok") else 1
            if args.slack_command == "status":
                _print(svc.status())
                return 0
            if args.slack_command == "ingest-event":
                _print(svc.handle_event_envelope(load_event_file(args.file)))
                return 0
            if args.slack_command == "finalize-session":
                _print(
                    svc.finalize_session(
                        session_id=args.session_id,
                        reason=args.reason,
                        message_count=args.message_count,
                    )
                )
                return 0
            if args.slack_command == "run":
                SlackSocketModeRunner(svc, reply_mode=args.reply_mode).run_forever()
                return 0

        if args.command == "peer":
            if args.peer_command == "serve":
                peer_args = ["--host", args.host, "--port", str(args.port)]
                if args.amo_home:
                    peer_args.extend(["--amo-home", str(args.amo_home)])
                return peer_server_main(peer_args)
            if args.amo_home:
                os.environ["AMO_HOME"] = str(args.amo_home)
            settings = Settings.load()
            if args.peer_command == "enable":
                runtime = PeerNetdRuntime(settings)
                _print(
                    runtime.start(
                        _peer_netd_options_from_args(args),
                        build_if_missing=not args.no_build,
                    )
                )
                return 0
            if args.peer_command == "netd":
                runtime = PeerNetdRuntime(settings)
                if args.netd_command == "build":
                    _print(runtime.build(args.out))
                    return 0
                if args.netd_command == "start":
                    _print(
                        runtime.start(
                            _peer_netd_options_from_args(args),
                            build_if_missing=not args.no_build,
                        )
                    )
                    return 0
                if args.netd_command == "stop":
                    _print(runtime.stop())
                    return 0
                if args.netd_command == "status":
                    _print(runtime.status())
                    return 0
                if args.netd_command == "install-service":
                    _print(
                        install_peer_netd_service(
                            settings,
                            _peer_netd_options_from_args(args),
                            _peer_netd_service_options_from_args(args),
                        )
                    )
                    return 0
                if args.netd_command == "uninstall-service":
                    _print(
                        uninstall_peer_netd_service(
                            settings,
                            _peer_netd_service_options_from_args(args),
                        )
                    )
                    return 0
                if args.netd_command == "service-status":
                    result = peer_netd_service_status(_peer_netd_service_options_from_args(args))
                    _print(result)
                    return 0 if result.get("ok") else 1
            if args.peer_command == "relay":
                runtime = PeerNetdRuntime(settings)
                if args.relay_command == "start":
                    result = runtime.start(
                        _peer_relay_options_from_args(args),
                        build_if_missing=not args.no_build,
                    )
                    _print(_with_relay_next_steps(result, args.namespace))
                    return 0
                if args.relay_command == "status":
                    _print(runtime.status())
                    return 0
            if args.peer_command == "doctor":
                result = peer_doctor(settings)
                _print(result)
                return 0 if result.get("ready") or not args.strict else 1
            svc = PeerService(settings)
            if args.peer_command == "init":
                _print(
                    svc.init_node(
                        node_id=args.node_id,
                        display_name=args.display_name,
                        capabilities=args.capability or None,
                    )
                )
                return 0
            if args.peer_command == "add":
                _print(
                    svc.add_peer(
                        node_id=args.node_id,
                        base_url=args.base_url,
                        peer_id=args.peer_id,
                        multiaddrs=args.multiaddr,
                        relay_addrs=args.relay_addr,
                        rendezvous_addr=args.rendezvous_addr,
                        rendezvous_namespace=args.rendezvous_namespace,
                        display_name=args.display_name,
                        capabilities=args.capability or None,
                        trust=args.trust,
                        shared_secret_env=args.shared_secret_env,
                    )
                )
                return 0
            if args.peer_command == "status":
                _print(svc.status())
                return 0
            if args.peer_command == "share-card":
                result = svc.share_card(
                    base_url=args.base_url,
                    rendezvous_addr=args.rendezvous_addr,
                    rendezvous_namespace=args.rendezvous_namespace,
                )
                if result.get("ok") and args.out:
                    args.out.parent.mkdir(parents=True, exist_ok=True)
                    args.out.write_text(json.dumps(result["card"], indent=2), encoding="utf-8")
                    result = result | {"path": str(args.out.resolve())}
                _print(result)
                return 0 if result.get("ok") else 1
            if args.peer_command == "import-card":
                card = json.loads(args.file.read_text(encoding="utf-8"))
                if not isinstance(card, dict):
                    raise ValueError("peer card file must contain a JSON object")
                _print(svc.import_card(card, trust=args.trust, shared_secret_env=args.shared_secret_env))
                return 0
            if args.peer_command == "create-invite":
                result = svc.create_peer_invite(
                    trust=args.trust,
                    shared_secret_env=args.shared_secret_env,
                    label=args.label,
                    base_url=args.base_url,
                    rendezvous_addr=args.rendezvous_addr,
                    rendezvous_namespace=args.rendezvous_namespace,
                    auto_approve=args.auto_approve,
                    expires_minutes=args.expires_minutes,
                    max_uses=args.max_uses,
                )
                if result.get("ok") and args.out:
                    args.out.write_text(json.dumps(result["invite"], indent=2), encoding="utf-8")
                    result["out"] = str(args.out)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.peer_command == "accept-invite":
                invite = decode_invite_code(args.code) if args.code else json.loads(args.file.read_text(encoding="utf-8"))
                if not isinstance(invite, dict):
                    raise ValueError("peer invite must contain a JSON object")
                result = svc.accept_peer_invite(
                    invite,
                    trust=args.trust,
                    shared_secret_env=args.shared_secret_env,
                    send_join_request=not args.no_send_join_request,
                )
                if result.get("ok") and args.response_out and result.get("response_card"):
                    args.response_out.write_text(json.dumps(result["response_card"], indent=2), encoding="utf-8")
                    result["response_out"] = str(args.response_out)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.peer_command == "join-requests":
                _print(svc.list_join_requests(status=args.status))
                return 0
            if args.peer_command == "approve-join":
                result = svc.approve_join_request(args.request_id)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.peer_command == "reject-join":
                result = svc.reject_join_request(args.request_id, reason=args.reason)
                _print(result)
                return 0 if result.get("ok") else 1
            if args.peer_command == "rooms":
                _print(svc.list_rooms())
                return 0
            if args.peer_command == "context":
                _print(svc.context_pack(args.room_id, viewer_node_id=args.viewer_node_id or None))
                return 0
            if args.peer_command == "append-message":
                _print(
                    svc.append_message(
                        room_id=args.room_id,
                        from_node_id=args.from_node_id,
                        to_node_ids=args.to_node_id,
                        message_type=args.type,
                        content=args.content,
                        citations=args.citation,
                        confidence=args.confidence,
                    )
                )
                return 0
            if args.peer_command == "send-message":
                _print(
                    svc.send_message_to_peer(
                        peer_id=args.peer_id,
                        room_id=args.room_id,
                        content=args.content,
                        message_type=args.type,
                        citations=args.citation,
                        confidence=args.confidence,
                    )
                )
                return 0
            if args.peer_command == "update-summary":
                _print(svc.update_summary(args.room_id, summary_md=args.summary))
                return 0
            if args.peer_command == "open-room":
                _print(svc.open_room(topic=args.topic, peer_ids=args.peer, send_invites=not args.no_send))
                return 0
            if args.peer_command == "poll-netd":
                if args.watch:
                    return _watch_peer_netd_inbox(
                        svc,
                        limit=args.limit,
                        interval_seconds=args.interval_seconds,
                        max_iterations=args.max_iterations,
                        fail_fast=args.fail_fast,
                    )
                _print(svc.process_netd_inbox(limit=args.limit))
                return 0

        if args.command in {
            "ingest-transcript",
            "ingest-hook",
            "import-codex-sessions",
            "rebuild-clean-db",
            "search",
            "context-pack",
            "timeline",
            "export",
            "import",
            "session-summary",
            "metrics",
            "rebuild-indexes",
            "print-codex-hooks",
        }:
            if args.command == "rebuild-clean-db":
                settings = Settings.load()
                result = _rebuild_clean_db(settings, args.out, args.codex_root, args.limit, args.force)
                _print({"ok": True, "result": result})
                return 0

            settings = Settings.load()
            svc = MemoryService(settings)
            try:
                svc.init_db()
                if args.command == "ingest-transcript":
                    result = svc.ingest_transcript(
                        agent=args.agent,
                        file_path=args.file,
                        session_id=args.session_id,
                        session_title=args.session_title,
                    )
                    _print({"ok": True, **result})
                elif args.command == "ingest-hook":
                    payload = json.loads(args.file.read_text(encoding="utf-8"))
                    result = svc.ingest_hook_payload(payload, default_agent=args.agent)
                    _print({"ok": True, **result})
                elif args.command == "import-codex-sessions":
                    result = svc.import_codex_sessions(
                        args.root,
                        limit=args.limit,
                        defer_vectors=args.defer_vectors,
                        skip_existing=not args.include_existing,
                    )
                    _print({"ok": True, "result": result})
                elif args.command == "print-codex-hooks":
                    _print({"ok": True, "hooks": _codex_hooks_snippet()})
                elif args.command == "search":
                    results = svc.search_memories(
                        args.query,
                        session_id=args.session_id,
                        limit=args.limit,
                        include_historical=args.include_historical,
                    )
                    _print({"ok": True, "count": len(results), "results": results})
                elif args.command == "context-pack":
                    pack = svc.build_context_pack(
                        args.query,
                        session_id=args.session_id,
                        budget_tokens=args.budget,
                        limit=args.limit,
                        include_historical=args.include_historical,
                    )
                    if args.format == "text":
                        print(pack["text"])
                    else:
                        _print({"ok": True, "result": pack})
                elif args.command == "timeline":
                    events = svc.timeline(args.session_id, limit=args.limit)
                    _print({"ok": True, "count": len(events), "events": events})
                elif args.command == "export":
                    rows = svc.export_snapshot(args.out, session_id=args.session_id)
                    _print({"ok": True, "rows": rows, "out": str(args.out.resolve())})
                elif args.command == "import":
                    rows = svc.import_snapshot(args.file)
                    _print({"ok": True, "rows": rows, "source": str(args.file.resolve())})
                elif args.command == "session-summary":
                    result = svc.generate_session_summary(args.session_id)
                    _print({"ok": True, "result": result})
                elif args.command == "metrics":
                    _print({"ok": True, "result": svc.inspect_metrics()})
                elif args.command == "rebuild-indexes":
                    _print({"ok": True, "result": svc.rebuild_indexes(force_vectors=args.force_vectors)})
            finally:
                svc.close()
            return 0

        if args.command == "graph-build-session":
            settings = Settings.load()
            graph_path = args.graph_path or default_session_graph_path(args.session_id)
            build_options = SessionGraphBuildOptions(
                session_id=args.session_id,
                graph_path=graph_path,
                repo_root=args.repo_root,
                commit=args.commit,
                evidence_paths=tuple(args.evidence_path or ()),
                transcript_paths=tuple(args.transcript_path or ()),
                file_paths=tuple(args.file_path or ()),
                text_embedding_model=args.text_embedding_model or settings.embedding_model,
                code_embedding_model=args.code_embedding_model or DEFAULT_CODE_EMBEDDING_MODEL,
                force=args.force,
                limit_events=args.limit_events,
            )
            if args.query or args.code_query:
                _print(
                    build_and_query_session_graph(
                        build_options,
                        query=args.query or None,
                        code_query=args.code_query or None,
                        limit=args.limit,
                    )
                )
            else:
                _print({"ok": True, "build": asdict(build_session_graph(build_options))})
            return 0

        if args.command == "graph-session-search":
            settings = Settings.load()
            result = query_session_graph(
                SessionGraphQueryOptions(
                    graph_path=args.graph_path,
                    query=args.query or None,
                    code_query=args.code_query or None,
                    text_embedding_model=args.text_embedding_model or settings.embedding_model,
                    code_embedding_model=args.code_embedding_model or DEFAULT_CODE_EMBEDDING_MODEL,
                    limit=args.limit,
                )
            )
            _print({"ok": True, "result": asdict(result)})
            return 0

        if args.command in {
            "graph-search",
            "graph-status",
            "graph-drain",
            "graph-drain-smoke",
            "graph-cleanup-noisy",
            "graph-consolidate",
            "graph-cache-status",
            "graph-rebuild-cache",
            "graph-retrieval-build",
            "graph-retrieval-embed",
            "graph-retrieve",
            "graph-finalize-session",
            "graph-rebuild-central",
            "graph-version-flow",
        }:
            settings = _settings_with_path_overrides(Settings.load(), args)
            if args.offline:
                graph = GraphRagService(settings)
                try:
                    if args.command == "graph-search":
                        result = graph.graph_search(
                            query=args.query,
                            limit=args.limit,
                            include_raw=args.include_raw,
                            include_historical=args.include_historical,
                        )
                    elif args.command == "graph-drain":
                        result = graph.drain_evidence(limit=args.limit, session_id=args.session_id, max_windows=args.max_windows)
                    elif args.command == "graph-drain-smoke":
                        result = graph.drain_evidence_smoke(limit=args.limit, max_windows=args.max_windows)
                    elif args.command == "graph-cleanup-noisy":
                        result = graph.cleanup_noisy_drafts(limit=args.limit, apply=args.apply)
                    elif args.command == "graph-consolidate":
                        result = graph.consolidate_graph(limit=args.limit, apply=args.apply)
                    elif args.command == "graph-cache-status":
                        result = graph.graph_cache_status()
                    elif args.command == "graph-rebuild-cache":
                        result = graph.rebuild_graph_cache(limit=args.limit)
                    elif args.command == "graph-retrieval-build":
                        result = graph.rebuild_retrieval_index(
                            db_path=args.db_path,
                            session_id=args.session_id,
                            limit=args.limit,
                            max_doc_chars=args.max_doc_chars,
                        )
                    elif args.command == "graph-retrieval-embed":
                        result = graph.embed_retrieval_index(
                            db_path=args.db_path,
                            session_id=args.session_id,
                            limit=args.limit,
                            model=args.model,
                            graph_scope=args.graph_scope,
                            rebuild_faiss=not args.no_faiss,
                        )
                    elif args.command == "graph-retrieve":
                        result = graph.retrieve_indexed_graph(
                            query=args.query,
                            db_path=args.db_path,
                            session_id=args.session_id,
                            limit=args.limit,
                            use_vector=not args.no_vector,
                            model=args.model,
                            graph_scope=args.graph_scope,
                            require_vector=args.require_vector,
                            include_answer=not args.no_answer,
                        )
                    elif args.command == "graph-finalize-session":
                        result = graph.finalize_session(
                            session_id=args.session_id,
                            commit=args.commit,
                            apply=args.apply,
                            limit=args.limit,
                            cwd=args.cwd or None,
                        )
                    elif args.command == "graph-rebuild-central":
                        result = graph.rebuild_central_from_evidence(
                            apply=args.apply,
                            backup_current=args.backup_current or args.apply,
                            limit=args.limit,
                            max_windows=args.max_windows,
                        )
                    elif args.command == "graph-version-flow":
                        result = graph.version_flow(commit=args.commit, session_id=args.session_id, limit=args.limit)
                    else:
                        result = graph.merge_status(session_id=args.session_id)
                    _print(result)
                finally:
                    graph.close()
            else:
                client_timeout = (
                    300
                    if args.command
                    in {
                        "graph-drain",
                        "graph-drain-smoke",
                        "graph-consolidate",
                        "graph-rebuild-cache",
                        "graph-retrieval-build",
                        "graph-retrieval-embed",
                        "graph-finalize-session",
                        "graph-rebuild-central",
                        "graph-version-flow",
                    }
                    else 60
                )
                client = DaemonClient.from_settings(settings, timeout_seconds=client_timeout)
                try:
                    if args.command == "graph-search":
                        result = client.post(
                            "/graph/search",
                            {
                                "query": args.query,
                                "limit": args.limit,
                                "include_raw": args.include_raw,
                                "include_historical": args.include_historical,
                            },
                        )
                    elif args.command == "graph-drain":
                        result = client.post(
                            "/graph/drain",
                            {"session_id": args.session_id, "limit": args.limit, "max_windows": args.max_windows},
                        )
                    elif args.command == "graph-drain-smoke":
                        result = client.post(
                            "/graph/drain-smoke",
                            {"limit": args.limit, "max_windows": args.max_windows},
                        )
                    elif args.command == "graph-cleanup-noisy":
                        result = client.post("/graph/cleanup-noisy", {"limit": args.limit, "apply": args.apply})
                    elif args.command == "graph-consolidate":
                        result = client.post("/graph/consolidate", {"limit": args.limit, "apply": args.apply})
                    elif args.command == "graph-cache-status":
                        result = client.get("/api/debug/graph-cache")
                    elif args.command == "graph-rebuild-cache":
                        result = client.post("/graph/rebuild-cache", {"limit": args.limit})
                    elif args.command == "graph-retrieval-build":
                        result = client.post(
                            "/graph/retrieval-build",
                            {
                                "session_id": args.session_id,
                                "limit": args.limit,
                                "max_doc_chars": args.max_doc_chars,
                                "db_path": str(args.db_path) if args.db_path else "",
                                "graph_path": str(args.graph_path) if args.graph_path else "",
                            },
                        )
                    elif args.command == "graph-retrieval-embed":
                        result = client.post(
                            "/graph/retrieval-embed",
                            {
                                "session_id": args.session_id,
                                "limit": args.limit,
                                "model": args.model,
                                "graph_scope": args.graph_scope,
                                "db_path": str(args.db_path) if args.db_path else "",
                                "graph_path": str(args.graph_path) if args.graph_path else "",
                                "rebuild_faiss": not args.no_faiss,
                            },
                        )
                    elif args.command == "graph-retrieve":
                        result = client.post(
                            "/graph/retrieve",
                            {
                                "query": args.query,
                                "session_id": args.session_id,
                                "limit": args.limit,
                                "model": args.model,
                                "graph_scope": args.graph_scope,
                                "db_path": str(args.db_path) if args.db_path else "",
                                "graph_path": str(args.graph_path) if args.graph_path else "",
                                "use_vector": not args.no_vector,
                                "require_vector": args.require_vector,
                                "include_answer": not args.no_answer,
                            },
                        )
                    elif args.command == "graph-finalize-session":
                        result = client.post(
                            "/graph/finalize-session",
                            {
                                "session_id": args.session_id,
                                "commit": args.commit,
                                "cwd": args.cwd or None,
                                "limit": args.limit,
                                "apply": args.apply,
                            },
                        )
                    elif args.command == "graph-rebuild-central":
                        result = client.post(
                            "/graph/rebuild-central",
                            {
                                "from_evidence": args.from_evidence,
                                "backup_current": args.backup_current or args.apply,
                                "limit": args.limit,
                                "max_windows": args.max_windows,
                                "apply": args.apply,
                            },
                        )
                    elif args.command == "graph-version-flow":
                        result = client.post(
                            "/graph/version-flow",
                            {"commit": args.commit, "session_id": args.session_id, "limit": args.limit},
                        )
                    else:
                        result = client.get("/api/graph/status", {"session_id": args.session_id})
                except DaemonUnavailable as exc:
                    _print(
                        {
                            "ok": False,
                            "requires_daemon": True,
                            "error": str(exc),
                            "hint": "Start the daemon with: python -m agent_memory_orchestrator.daemon",
                        }
                    )
                    return 1
                _print(result)
            return 0

        if args.command == "debug":
            settings = Settings.load()
            if args.debug_command == "hooks":
                _print(debug_hooks(settings))
                return 0
            if args.debug_command == "qwen":
                _print(debug_qwen(settings, sample=args.sample))
                return 0
            if args.debug_command in {"drain", "retrieval", "graph"}:
                client = DaemonClient.from_settings(settings, timeout_seconds=30)
                try:
                    if args.debug_command == "drain":
                        _print(client.get("/api/debug/drain", {"session_id": args.session_id}))
                    elif args.debug_command == "graph":
                        _print(client.get("/api/debug/graph", {"session_id": args.session_id}))
                    else:
                        _print(client.post("/graph/search", {"query": args.query, "limit": args.limit, "debug": True}))
                except DaemonUnavailable as exc:
                    _print({"ok": False, "requires_daemon": True, "error": str(exc)})
                    return 1
                return 0

        if args.command == "skill-checkpoint":
            if getattr(args, "amo_home", None):
                os.environ["AMO_HOME"] = str(args.amo_home.expanduser().resolve())
            if args.skill_checkpoint_command == "extract":
                settings = Settings.load()
                packet = json.loads(args.packet.read_text(encoding="utf-8"))
                report = run_local_skill_checkpoint_extraction(
                    packet=packet,
                    settings=settings,
                    out_dir=args.out_dir,
                    num_ctx=args.num_ctx,
                    num_predict=args.num_predict,
                    timeout_seconds=args.timeout_seconds,
                    auto_repair_validation_refs=not args.no_auto_repair_validation_refs,
                )
            elif args.skill_checkpoint_command == "mark":
                settings = Settings.load()
                report = mark_skill_checkpoint(
                    settings=settings,
                    agent=args.agent,
                    session_id=args.session_id,
                    note=args.note,
                    mode=args.mode,
                    cwd=args.cwd,
                )
            elif args.skill_checkpoint_command == "status":
                settings = Settings.load()
                report = list_skill_checkpoints(settings, limit=args.limit)
            elif args.skill_checkpoint_command == "finalize":
                packet = json.loads(args.packet.read_text(encoding="utf-8"))
                result = json.loads(args.result.read_text(encoding="utf-8"))
                report = write_skill_checkpoint_outputs(
                    result=result,
                    packet=packet,
                    out_dir=args.out_dir,
                    auto_repair_validation_refs=not args.no_auto_repair_validation_refs,
                )
            else:
                parser.error(f"unknown skill-checkpoint command: {args.skill_checkpoint_command}")
                return 2
            ok = bool(report.get("ok", report.get("status") == "accepted"))
            _print({"ok": ok, "result": report})
            return 0 if ok else 1

        settings = Settings.load()
        orch = OrchestratorService(settings)
        try:
            if args.command == "orchestrate-start":
                payload = orch.start(session_id=args.session_id, title=args.title)
            elif args.command == "orchestrate-submit":
                payload = orch.submit(
                    session_id=args.session_id,
                    agent=args.agent,
                    summary=args.summary,
                    confidence=args.confidence,
                    artifact_uri=args.artifact_uri,
                    blocking_issues=args.blocking_issue,
                )
            elif args.command == "orchestrate-status":
                payload = orch.status(session_id=args.session_id)
            elif args.command == "orchestrate-decision":
                payload = orch.user_decision(
                    session_id=args.session_id,
                    decision=args.decision,
                    notes=args.notes,
                    decided_by=args.decided_by,
                )
            else:
                parser.error(f"unknown command: {args.command}")
                return 2
            _print({"ok": True, "result": payload})
        finally:
            orch.close()
        return 0
    except (DaemonUnavailable, GraphBackendUnavailable, QwenUnavailable) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1
    except Exception as exc:  # pragma: no cover
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 1


def _codex_hooks_snippet() -> dict:
    command = "python -m agent_memory_orchestrator.hook --agent codex"
    return {
        "format": "toml",
        "snippet": "\n".join(
            [
                "[features]",
                "codex_hooks = true",
                "",
                "[[hooks.SessionStart]]",
                'matcher = "startup|resume|clear"',
                "[[hooks.SessionStart.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO starting graph capture"',
                "",
                "[[hooks.UserPromptSubmit]]",
                "[[hooks.UserPromptSubmit.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing prompt evidence"',
                "",
                "[[hooks.PostToolUse]]",
                'matcher = "*"',
                "[[hooks.PostToolUse.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing tool evidence"',
                "",
                "[[hooks.Stop]]",
                "[[hooks.Stop.hooks]]",
                'type = "command"',
                f"command = {json.dumps(command)}",
                "timeout = 30",
                'statusMessage = "AMO capturing session stop"',
            ]
        ),
    }


def _settings_with_path_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    updates = {}
    db_path = getattr(args, "db_path", None)
    graph_path = getattr(args, "graph_path", None)
    retrieval_command = getattr(args, "command", "") in {
        "graph-retrieval-build",
        "graph-retrieval-embed",
        "graph-retrieve",
    }
    if db_path:
        updates["db_path"] = Path(db_path).expanduser().resolve()
    if graph_path:
        updates["graph_path"] = Path(graph_path).expanduser().resolve()
    elif retrieval_command and settings.retrieval_graph_path is not None:
        updates["graph_path"] = settings.retrieval_graph_path
    if not updates:
        return settings
    for key in ("db_path", "graph_path"):
        path = updates.get(key)
        if isinstance(path, Path):
            path.parent.mkdir(parents=True, exist_ok=True)
    return replace(settings, **updates)


def _watch_peer_netd_inbox(
    svc: PeerService,
    *,
    limit: int | None,
    interval_seconds: float,
    max_iterations: int = 0,
    fail_fast: bool = False,
) -> int:
    if interval_seconds <= 0:
        raise ValueError("--interval-seconds must be positive")
    iterations = 0
    try:
        while True:
            try:
                _print_line(svc.process_netd_inbox(limit=limit))
            except Exception as exc:
                _print_line({"ok": False, "error": str(exc), "watching": not fail_fast})
                if fail_fast:
                    return 1
            iterations += 1
            if max_iterations and iterations >= max_iterations:
                return 0
            time.sleep(interval_seconds)
    except KeyboardInterrupt:
        _print_line({"ok": True, "stopped": True, "reason": "interrupted"})
        return 0


def _add_peer_netd_start_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--node-id", default="amo-node", help="Stable AMO node id advertised by the sidecar.")
    parser.add_argument("--listen", default="/ip4/0.0.0.0/tcp/0", help="libp2p listen multiaddr.")
    parser.add_argument("--api", default="127.0.0.1:8788", help="Local sidecar API host:port. Must be fixed for managed start.")
    parser.add_argument("--store-path", default="", help="Optional sidecar JSONL inbox path. Defaults under AMO_HOME/.peer/netd.")
    parser.add_argument(
        "--shared-secret-env",
        default="",
        help="Environment variable containing the shared HMAC secret used by peer-netd.",
    )
    parser.add_argument("--require-signature", action="store_true", help="Reject unsigned incoming peer envelopes.")
    parser.add_argument("--bootstrap", action="append", default=[], help="Bootstrap peer multiaddr. Repeat for multiple peers.")
    parser.add_argument("--static-relay", action="append", default=[], help="Circuit relay multiaddr. Repeat for multiple relays.")
    parser.add_argument("--mdns", action="store_true", help="Enable LAN mDNS discovery.")
    parser.add_argument("--mdns-service", default="_amo-peer._udp", help="mDNS service tag.")
    parser.add_argument("--rendezvous-server", action="store_true", help="Serve AMO rendezvous registration/discovery streams.")
    parser.add_argument("--rendezvous-addr", default="", help="Rendezvous node multiaddr to register with after startup.")
    parser.add_argument("--rendezvous-namespace", default="", help="Rendezvous namespace to register this node under.")
    parser.add_argument("--rendezvous-ttl-seconds", type=int, default=7200, help="Rendezvous registration TTL.")
    parser.add_argument("--relay-service", action="store_true", help="Serve libp2p circuit relay v2 when reachable.")
    parser.add_argument("--nat-service", action="store_true", help="Help peers determine reachability.")
    parser.add_argument("--auto-relay", action="store_true", help="Enable AutoRelay; usually paired with --static-relay.")
    parser.add_argument("--hole-punching", action="store_true", help="Enable libp2p DCUtR hole punching.")
    parser.add_argument("--force-private", action="store_true", help="Force private reachability for relay tests.")
    parser.add_argument("--force-public", action="store_true", help="Force public reachability for relay-service tests.")
    parser.add_argument(
        "--advertise-localhost-dns",
        action="store_true",
        help="Local smoke only: advertise 127.0.0.1 as dns4/localhost.",
    )
    parser.add_argument(
        "--advertise-addr",
        action="append",
        default=[],
        help="Public libp2p listen multiaddr to advertise, e.g. /ip4/1.2.3.4/tcp/4001.",
    )
    parser.add_argument("--no-build", action="store_true", help="Do not build peer-netd automatically if the binary is missing.")


def _add_peer_netd_watch_service_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--with-watch",
        action="store_true",
        help="Also install, uninstall, or inspect the poll-netd --watch startup entry.",
    )
    parser.add_argument(
        "--watch-service-name",
        default="",
        help="Optional OS startup name for the poll-netd --watch entry.",
    )


def _peer_netd_options_from_args(args: argparse.Namespace) -> PeerNetdLaunchOptions:
    return PeerNetdLaunchOptions(
        node_id=args.node_id,
        listen_addr=args.listen,
        api_addr=args.api,
        store_path=args.store_path,
        shared_secret_env=args.shared_secret_env,
        require_signature=args.require_signature,
        bootstrap_addrs=tuple(args.bootstrap or []),
        static_relays=tuple(args.static_relay or []),
        mdns=args.mdns,
        mdns_service=args.mdns_service,
        rendezvous_server=args.rendezvous_server,
        relay_service=args.relay_service,
        nat_service=args.nat_service,
        auto_relay=args.auto_relay,
        hole_punching=args.hole_punching,
        force_private=args.force_private,
        force_public=args.force_public,
        advertise_localhost_dns=args.advertise_localhost_dns,
        advertise_addrs=tuple(args.advertise_addr or []),
        rendezvous_addr=args.rendezvous_addr,
        rendezvous_namespace=args.rendezvous_namespace,
        rendezvous_ttl_seconds=args.rendezvous_ttl_seconds,
    )


def _peer_relay_options_from_args(args: argparse.Namespace) -> PeerNetdLaunchOptions:
    return PeerNetdLaunchOptions(
        node_id=args.node_id,
        listen_addr=args.listen,
        api_addr=args.api,
        store_path=args.store_path,
        rendezvous_server=True,
        relay_service=True,
        nat_service=True,
        force_public=True,
        advertise_addrs=tuple(args.advertise_addr or ()),
    )


def _with_relay_next_steps(result: dict[str, Any], namespace: str) -> dict[str, Any]:
    status = result.get("status") if isinstance(result.get("status"), dict) else {}
    health = result.get("health") if isinstance(result.get("health"), dict) else status.get("health", {})
    addrs = [str(item) for item in health.get("listen_addrs", []) if str(item).strip()]
    relay_addr = addrs[0] if addrs else ""
    client_enable_args = [
        "peer",
        "enable",
        "--static-relay",
        relay_addr or "<relay-multiaddr>",
        "--auto-relay",
        "--hole-punching",
        "--rendezvous-addr",
        relay_addr or "<relay-multiaddr>",
        "--rendezvous-namespace",
        namespace,
    ]
    invite_flags = [
        "--rendezvous-addr",
        relay_addr or "<relay-multiaddr>",
        "--rendezvous-namespace",
        namespace,
    ]
    return result | {
        "relay": {
            "relay_multiaddr": relay_addr,
            "rendezvous_addr": relay_addr,
            "rendezvous_namespace": namespace,
            "client_enable_args": client_enable_args,
            "create_invite_flags": invite_flags,
            "notes": [
                "Run this helper on an always-on public host or VPS with inbound TCP open for the listen port.",
                "Client devices should start peer netd with --static-relay before creating or accepting invites.",
                "The relay/rendezvous node carries transport streams and discovery records only; AMO room policy and memory stay on user devices.",
            ],
        }
    }


def _peer_netd_service_options_from_args(args: argparse.Namespace) -> PeerNetdServiceOptions:
    return PeerNetdServiceOptions(
        service_name=args.service_name,
        apply=getattr(args, "apply", False),
        with_watcher=getattr(args, "with_watch", False),
        watch_service_name=getattr(args, "watch_service_name", ""),
    )


def _add_model_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--preset",
        choices=["cpu-light", "cpu-balanced", "gpu-quality"],
        default="cpu-balanced",
        help="Hardware-oriented model preset.",
    )
    parser.add_argument("--embedding-model", help="Override preset embedding model.")
    parser.add_argument("--reranker-model", help="Override preset reranker model.")
    parser.add_argument("--qwen-model", help="Override preset Ollama Qwen model.")


def _confirm(prompt: str) -> bool:
    if not sys.stdin.isatty():
        return False
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _summarize_install_plan(plan: dict) -> dict:
    operations = []
    for op in plan["operations"]:
        path = Path(op["path"])
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        after = op["after"]
        safe_after = redact_secrets(after)[0]
        operations.append(
            {
                "target": op["target"],
                "path": op["path"],
                "description": op["description"],
                "exists": op["exists"],
                "changed": before != after,
                "after_preview": safe_after[:2000],
                "after_truncated": len(safe_after) > 2000,
            }
        )
    return {
        "target": plan["target"],
        "targets": plan["targets"],
        "user_home": plan["user_home"],
        "amo_home": plan["amo_home"],
        "models": plan["models"],
        "operations": operations,
        "notes": plan["notes"],
    }


def _rebuild_clean_db(settings: Settings, out_path: Path, codex_root: Path, limit: int, force: bool) -> dict:
    target = out_path.resolve()
    if target.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing DB without --force: {target}")
    if force:
        for path in (target, target.with_name(target.name + "-wal"), target.with_name(target.name + "-shm")):
            if path.exists():
                path.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean_settings = replace(settings, db_path=target)
    svc = MemoryService(clean_settings)
    try:
        svc.init_db()
        result = svc.import_codex_sessions(codex_root, limit=limit)
        indexes = svc.rebuild_indexes(force_vectors=False)
        return {
            "out": str(target),
            "codex_root": str(codex_root.resolve()),
            "import": result,
            "indexes": indexes,
        }
    finally:
        svc.close()


if __name__ == "__main__":
    raise SystemExit(main())

