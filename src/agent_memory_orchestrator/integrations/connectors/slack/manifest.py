from __future__ import annotations

import json
from urllib.parse import quote
from typing import Any


DEFAULT_BOT_SCOPES = [
    "app_mentions:read",
    "channels:history",
    "groups:history",
    "im:history",
    "mpim:history",
    "chat:write",
    "users:read",
]


DEFAULT_BOT_EVENTS = [
    "app_mention",
    "message.channels",
    "message.groups",
    "message.im",
    "message.mpim",
]


def build_slack_manifest(*, app_name: str = "Agent Memory Orchestrator") -> dict[str, Any]:
    """Build a dependency-free Slack app manifest for local Socket Mode."""

    return {
        "display_information": {"name": app_name},
        "features": {
            "bot_user": {
                "display_name": "AMO",
                "always_online": False,
            }
        },
        "oauth_config": {
            "scopes": {
                "bot": DEFAULT_BOT_SCOPES,
            }
        },
        "settings": {
            "event_subscriptions": {
                "bot_events": DEFAULT_BOT_EVENTS,
            },
            "interactivity": {"is_enabled": False},
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }


def slack_manifest_json(*, app_name: str = "Agent Memory Orchestrator") -> str:
    return json.dumps(build_slack_manifest(app_name=app_name), indent=2, sort_keys=True) + "\n"


def slack_manifest_setup_url(*, app_name: str = "Agent Memory Orchestrator") -> str:
    manifest = json.dumps(build_slack_manifest(app_name=app_name), ensure_ascii=False, sort_keys=True)
    return f"https://api.slack.com/apps?new_app=1&manifest_json={quote(manifest, safe='')}"
