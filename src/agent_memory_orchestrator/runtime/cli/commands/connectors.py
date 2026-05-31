"""CLI command group handling for connector operations."""

from __future__ import annotations

from collections.abc import Callable
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


__all__ = ["CONNECTOR_COMMANDS", "SLACK_SUBCOMMANDS", "handle_connector_command"]
