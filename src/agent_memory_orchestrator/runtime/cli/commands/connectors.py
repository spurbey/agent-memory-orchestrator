"""CLI command group handling for connector operations."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from ....core.config import Settings
from ....integrations.connectors.slack import SlackConnectorService
from ....integrations.connectors.slack.manifest import slack_manifest_json, slack_manifest_setup_url
from ....integrations.connectors.slack.service import load_event_file
from ....integrations.connectors.slack.socket_mode import SlackSocketModeRunner
from ....integrations.connectors.slack.wizard import run_slack_setup_wizard

CONNECTOR_COMMANDS = ("slack",)

SLACK_SUBCOMMANDS = (
    "manifest",
    "setup-link",
    "bootstrap",
    "setup",
    "setup-wizard",
    "status",
    "ingest-event",
    "finalize-session",
    "run",
)


def add_connector_subcommands(sub: Any) -> None:
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


def handle_connector_command(args: Any, *, emit: Callable[[object], None]) -> int | None:
    """Run connector CLI commands."""
    if args.command != "slack":
        return None

    if args.slack_command == "manifest":
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(slack_manifest_json(app_name=args.app_name), encoding="utf-8")
            emit({"ok": True, "path": str(args.out.resolve())})
        else:
            print(slack_manifest_json(app_name=args.app_name), end="")
        return 0
    if args.slack_command == "setup-link":
        emit(
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
        emit(result)
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
        emit(result)
        return 0 if result.get("ok") else 1
    if args.slack_command == "setup-wizard":
        result = run_slack_setup_wizard(
            svc,
            default_save_tokens=not args.no_save_tokens,
            default_validate_tokens=not args.skip_token_validation,
        )
        emit(result)
        return 0 if result.get("ok") else 1
    if args.slack_command == "status":
        emit(svc.status())
        return 0
    if args.slack_command == "ingest-event":
        emit(svc.handle_event_envelope(load_event_file(args.file)))
        return 0
    if args.slack_command == "finalize-session":
        emit(
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
    return None


__all__ = ["CONNECTOR_COMMANDS", "SLACK_SUBCOMMANDS", "add_connector_subcommands", "handle_connector_command"]
