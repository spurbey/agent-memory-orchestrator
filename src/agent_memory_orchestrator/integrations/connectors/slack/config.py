from __future__ import annotations

import json
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ....core.config import Settings


APP_TOKEN_ENV = "AMO_SLACK_APP_TOKEN"
BOT_TOKEN_ENV = "AMO_SLACK_BOT_TOKEN"


@dataclass(slots=True, frozen=True)
class SlackConfig:
    enabled: bool = False
    mode: str = "socket_mode"
    team_id: str = ""
    bot_user_id: str = ""
    capture_user_ids: tuple[str, ...] = ()
    allowed_channels: tuple[str, ...] = ()
    reply_only_when_mentioned: bool = True
    session_idle_minutes: int = 30
    app_token: str = ""
    bot_token: str = ""

    def without_tokens(self) -> "SlackConfig":
        return replace(self, app_token="", bot_token="")

    def public_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "team_id": self.team_id,
            "bot_user_id": self.bot_user_id,
            "capture_user_ids": list(self.capture_user_ids),
            "allowed_channels": list(self.allowed_channels),
            "reply_only_when_mentioned": self.reply_only_when_mentioned,
            "session_idle_minutes": self.session_idle_minutes,
            "has_app_token": bool(self.app_token),
            "has_bot_token": bool(self.bot_token),
        }

    def config_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "team_id": self.team_id,
            "bot_user_id": self.bot_user_id,
            "capture_user_ids": list(self.capture_user_ids),
            "allowed_channels": list(self.allowed_channels),
            "reply_only_when_mentioned": self.reply_only_when_mentioned,
            "session_idle_minutes": self.session_idle_minutes,
        }


def load_slack_config(settings: Settings) -> SlackConfig:
    config = _load_config_json(settings)
    slack = config.get("slack") if isinstance(config.get("slack"), dict) else {}
    secrets = _load_secret_json(settings)
    loaded = SlackConfig(
        enabled=_bool(_pick(slack, "enabled", False), default=False),
        mode=str(_pick(slack, "mode", "socket_mode") or "socket_mode"),
        team_id=str(_pick(slack, "team_id", "") or ""),
        bot_user_id=str(_pick(slack, "bot_user_id", "") or ""),
        capture_user_ids=tuple(_string_list(_pick(slack, "capture_user_ids", []))),
        allowed_channels=tuple(_string_list(_pick(slack, "allowed_channels", []))),
        reply_only_when_mentioned=_bool(_pick(slack, "reply_only_when_mentioned", True), default=True),
        session_idle_minutes=max(1, int(_pick(slack, "session_idle_minutes", 30) or 30)),
        app_token=str(secrets.get("app_token") or ""),
        bot_token=str(secrets.get("bot_token") or ""),
    )
    return _apply_env_overrides(loaded)


def write_slack_config(
    settings: Settings,
    config: SlackConfig,
    *,
    app_token: str = "",
    bot_token: str = "",
    save_tokens: bool = False,
) -> dict[str, Any]:
    raw = _load_config_json(settings)
    raw["slack"] = config.without_tokens().config_dict()
    path = config_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(raw, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    secret_written = False
    if save_tokens:
        secret_path = slack_secret_path(settings)
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        current = _load_secret_json(settings)
        if app_token:
            current["app_token"] = app_token
        if bot_token:
            current["bot_token"] = bot_token
        secret_path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        secret_written = True
    return {
        "config_path": str(path),
        "secret_path": str(slack_secret_path(settings)),
        "secret_written": secret_written,
    }


def config_path(settings: Settings) -> Path:
    raw = Path(os.getenv("AMO_CONFIG_PATH", settings.home / "config.json"))
    if not raw.is_absolute():
        raw = (settings.home / raw).resolve()
    return raw


def slack_secret_path(settings: Settings) -> Path:
    return settings.home / ".secrets" / "slack.json"


def token_presence(config: SlackConfig) -> dict[str, bool]:
    return {
        "app_token": bool(config.app_token),
        "bot_token": bool(config.bot_token),
    }


def validate_token_prefixes(app_token: str, bot_token: str) -> dict[str, Any]:
    errors: list[str] = []
    if app_token and not app_token.startswith("xapp-"):
        errors.append("app_token must start with xapp-")
    if bot_token and not bot_token.startswith("xoxb-"):
        errors.append("bot_token must start with xoxb-")
    return {"ok": not errors, "errors": errors}


def _load_config_json(settings: Settings) -> dict[str, Any]:
    path = config_path(settings)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _load_secret_json(settings: Settings) -> dict[str, Any]:
    path = slack_secret_path(settings)
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return payload if isinstance(payload, dict) else {}


def _apply_env_overrides(config: SlackConfig) -> SlackConfig:
    updates: dict[str, Any] = {}
    if APP_TOKEN_ENV in os.environ:
        updates["app_token"] = os.environ[APP_TOKEN_ENV]
    if BOT_TOKEN_ENV in os.environ:
        updates["bot_token"] = os.environ[BOT_TOKEN_ENV]
    if "AMO_SLACK_TEAM_ID" in os.environ:
        updates["team_id"] = os.environ["AMO_SLACK_TEAM_ID"]
    if "AMO_SLACK_BOT_USER_ID" in os.environ:
        updates["bot_user_id"] = os.environ["AMO_SLACK_BOT_USER_ID"]
    if "AMO_SLACK_CAPTURE_USER_IDS" in os.environ:
        updates["capture_user_ids"] = tuple(_string_list(os.environ["AMO_SLACK_CAPTURE_USER_IDS"]))
    if "AMO_SLACK_ALLOWED_CHANNELS" in os.environ:
        updates["allowed_channels"] = tuple(_string_list(os.environ["AMO_SLACK_ALLOWED_CHANNELS"]))
    if "AMO_SLACK_SESSION_IDLE_MINUTES" in os.environ:
        updates["session_idle_minutes"] = max(1, int(os.environ["AMO_SLACK_SESSION_IDLE_MINUTES"]))
    return replace(config, **updates) if updates else config


def _pick(config: dict[str, Any], key: str, default: object) -> object:
    env_name = f"AMO_SLACK_{key.upper()}"
    if env_name in os.environ:
        return os.environ[env_name]
    return config.get(key, default)


def _bool(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _string_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        raw = value.replace(";", ",").split(",")
    elif isinstance(value, list | tuple | set):
        raw = list(value)
    else:
        raw = [value]
    return [str(item).strip() for item in raw if str(item).strip()]
