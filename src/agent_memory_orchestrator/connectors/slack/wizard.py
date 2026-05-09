from __future__ import annotations

import getpass
from collections.abc import Callable
from typing import Any

from .service import SlackConnectorService


InputFn = Callable[[str], str]
SecretFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def run_slack_setup_wizard(
    service: SlackConnectorService,
    *,
    input_fn: InputFn = input,
    secret_fn: SecretFn = getpass.getpass,
    output_fn: OutputFn = print,
    default_save_tokens: bool = True,
    default_validate_tokens: bool = True,
) -> dict[str, Any]:
    output_fn("AMO Slack setup wizard")
    output_fn("Paste tokens locally. They will not be printed.")

    app_token = _secret(
        secret_fn,
        "Slack app-level Socket Mode token (xapp-...)",
        default=service.config.app_token,
    )
    bot_token = _secret(
        secret_fn,
        "Slack Bot User OAuth token (xoxb-...)",
        default=service.config.bot_token,
    )
    validate_now = _confirm(input_fn, "Validate tokens with Slack now?", default=default_validate_tokens)
    validation = service.validate_tokens(app_token=app_token, bot_token=bot_token, skip_network=not validate_now)
    if not validation.get("ok"):
        return {"ok": False, "stage": "validate_tokens", "validation": validation}

    auth_test = _auth_test_payload(validation)
    team_default = str(auth_test.get("team_id") or service.config.team_id or "")
    bot_user_default = str(auth_test.get("user_id") or service.config.bot_user_id or "")
    team_id = _prompt(input_fn, "Slack team ID", default=team_default)
    bot_user_id = _prompt(input_fn, "Bot user ID", default=bot_user_default)
    if not bot_user_id:
        return {
            "ok": False,
            "stage": "bot_user_id",
            "error": "Bot user ID is required so AMO can detect @bot mentions.",
            "hint": "Run validation or paste the bot user ID from the Slack app profile.",
        }

    capture_user_ids = _csv(_prompt(input_fn, "Slack user IDs to capture automatically, comma-separated", default=""))
    allowed_channels = _csv(_prompt(input_fn, "Restrict to channel IDs, comma-separated, blank for all", default=""))
    save_tokens = _confirm(input_fn, "Save tokens locally under AMO_HOME/.secrets/slack.json?", default=default_save_tokens)

    result = service.setup(
        team_id=team_id,
        bot_user_id=bot_user_id,
        capture_user_ids=capture_user_ids,
        allowed_channels=allowed_channels,
        app_token=app_token,
        bot_token=bot_token,
        save_tokens=save_tokens,
        skip_token_validation=True,
    )
    result["wizard"] = {
        "validated_with_slack": validate_now,
        "derived_team_id": bool(team_default),
        "derived_bot_user_id": bool(bot_user_default),
        "capture_user_count": len(capture_user_ids),
        "allowed_channel_count": len(allowed_channels),
    }
    return result


def _secret(secret_fn: SecretFn, label: str, *, default: str = "") -> str:
    suffix = " [already configured, press Enter to keep]" if default else ""
    value = secret_fn(f"{label}{suffix}: ").strip()
    return value or default


def _prompt(input_fn: InputFn, label: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input_fn(f"{label}{suffix}: ").strip()
    return value or default


def _confirm(input_fn: InputFn, label: str, *, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input_fn(f"{label} [{suffix}]: ").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y", "on"}


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def _auth_test_payload(validation: dict[str, Any]) -> dict[str, Any]:
    network = validation.get("network") if isinstance(validation.get("network"), dict) else {}
    auth = network.get("auth_test") if isinstance(network.get("auth_test"), dict) else {}
    return auth
