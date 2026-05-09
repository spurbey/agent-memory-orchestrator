from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from ...config import Settings
from ...raw_evidence import RawEvidenceStore
from .client import SlackApiClient, SlackApiError
from .config import SlackConfig, load_slack_config, token_presence, validate_token_prefixes, write_slack_config
from .events import (
    finalize_connector_event,
    message_to_connector_event,
    parse_message_envelope,
    should_capture_message,
    should_reply_message,
)
from .manifest import build_slack_manifest, slack_manifest_json


class SlackConnectorError(RuntimeError):
    pass


class SlackConnectorService:
    def __init__(
        self,
        settings: Settings,
        *,
        config: SlackConfig | None = None,
        evidence_store: RawEvidenceStore | None = None,
        client: SlackApiClient | None = None,
    ) -> None:
        self.settings = settings
        self.config = config or load_slack_config(settings)
        self.evidence = evidence_store or RawEvidenceStore(settings.evidence_dir)
        self.client = client or SlackApiClient(app_token=self.config.app_token, bot_token=self.config.bot_token)

    def manifest(self, *, app_name: str = "Agent Memory Orchestrator") -> dict[str, Any]:
        return build_slack_manifest(app_name=app_name)

    def write_manifest(self, path: Path, *, app_name: str = "Agent Memory Orchestrator") -> dict[str, Any]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(slack_manifest_json(app_name=app_name), encoding="utf-8")
        return {"ok": True, "path": str(path.resolve())}

    def setup(
        self,
        *,
        team_id: str = "",
        bot_user_id: str = "",
        capture_user_ids: list[str] | None = None,
        allowed_channels: list[str] | None = None,
        session_idle_minutes: int = 30,
        app_token: str = "",
        bot_token: str = "",
        save_tokens: bool = False,
        skip_token_validation: bool = False,
    ) -> dict[str, Any]:
        app_token = app_token or self.config.app_token
        bot_token = bot_token or self.config.bot_token
        validation = self.validate_tokens(app_token=app_token, bot_token=bot_token, skip_network=skip_token_validation)
        if not validation["ok"]:
            return {"ok": False, "validation": validation}

        configured = SlackConfig(
            enabled=True,
            mode="socket_mode",
            team_id=team_id or self.config.team_id,
            bot_user_id=bot_user_id or self.config.bot_user_id,
            capture_user_ids=tuple(capture_user_ids if capture_user_ids is not None else self.config.capture_user_ids),
            allowed_channels=tuple(allowed_channels if allowed_channels is not None else self.config.allowed_channels),
            reply_only_when_mentioned=True,
            session_idle_minutes=max(1, int(session_idle_minutes or self.config.session_idle_minutes or 30)),
            app_token=app_token,
            bot_token=bot_token,
        )
        write_result = write_slack_config(
            self.settings,
            configured,
            app_token=app_token,
            bot_token=bot_token,
            save_tokens=save_tokens,
        )
        self.config = configured
        self.client = SlackApiClient(app_token=app_token, bot_token=bot_token)
        return {
            "ok": True,
            "config": configured.public_dict(),
            "validation": validation,
            **write_result,
            "token_hint": None
            if save_tokens
            else "Tokens were not saved. Export AMO_SLACK_APP_TOKEN and AMO_SLACK_BOT_TOKEN before running.",
        }

    def status(self) -> dict[str, Any]:
        prefix_check = validate_token_prefixes(self.config.app_token, self.config.bot_token)
        return {
            "ok": True,
            "config": self.config.public_dict(),
            "tokens": token_presence(self.config),
            "prefix_check": prefix_check,
        }

    def validate_tokens(self, *, app_token: str, bot_token: str, skip_network: bool = False) -> dict[str, Any]:
        prefix_check = validate_token_prefixes(app_token, bot_token)
        if not prefix_check["ok"]:
            return {"ok": False, "prefix": prefix_check, "network": None}
        if skip_network:
            return {"ok": True, "prefix": prefix_check, "network": "skipped"}
        client = SlackApiClient(app_token=app_token, bot_token=bot_token)
        try:
            auth = client.auth_test() if bot_token else {}
        except SlackApiError as exc:
            return {"ok": False, "prefix": prefix_check, "network": str(exc)}
        return {"ok": True, "prefix": prefix_check, "network": {"auth_test": _safe_auth_test(auth)}}

    def handle_event_envelope(self, envelope: dict[str, Any]) -> dict[str, Any]:
        if not self.config.enabled:
            return {"ok": True, "captured": False, "reason": "slack_connector_disabled"}
        message = parse_message_envelope(envelope)
        if message is None:
            return {"ok": True, "captured": False, "reason": "unsupported_slack_event"}
        should_capture, reason = should_capture_message(message, self.config)
        if not should_capture:
            return {
                "ok": True,
                "captured": False,
                "reason": reason,
                "session_id": message.session_id,
                "external_id": message.external_id,
            }
        reply_required = should_reply_message(message, self.config)
        connector_event = message_to_connector_event(message, capture_reason=reason, reply_required=reply_required)
        evidence = self.evidence.append(
            connector_event.as_evidence_payload(),
            session_id=connector_event.session_id,
            source_app=connector_event.source_app,
            event_name=connector_event.event_type,
        )
        return {
            "ok": True,
            "captured": True,
            "reason": reason,
            "reply_required": reply_required,
            "session_id": connector_event.session_id,
            "external_id": connector_event.external_id,
            "evidence": evidence.as_dict(),
        }

    def finalize_session(self, *, session_id: str, reason: str = "idle_timeout", message_count: int = 0) -> dict[str, Any]:
        connector_event = finalize_connector_event(session_id=session_id, reason=reason, message_count=message_count)
        evidence = self.evidence.append(
            connector_event.as_evidence_payload(),
            session_id=connector_event.session_id,
            source_app=connector_event.source_app,
            event_name=connector_event.event_type,
        )
        return {
            "ok": True,
            "session_id": session_id,
            "event_type": connector_event.event_type,
            "evidence": evidence.as_dict(),
            "next_step": "Run graph-drain for this session to create the cleaned window and GraphDelta.",
        }

    def post_ack_reply(self, *, channel: str, thread_ts: str = "") -> dict[str, Any]:
        text = "AMO captured this mention locally. I will answer only on tagged Slack messages."
        return self.client.post_message(channel=channel, text=text, thread_ts=thread_ts)


def config_from_args(
    base: SlackConfig,
    *,
    team_id: str = "",
    bot_user_id: str = "",
    capture_user_ids: list[str] | None = None,
    allowed_channels: list[str] | None = None,
    session_idle_minutes: int | None = None,
) -> SlackConfig:
    return replace(
        base,
        team_id=team_id or base.team_id,
        bot_user_id=bot_user_id or base.bot_user_id,
        capture_user_ids=tuple(capture_user_ids if capture_user_ids is not None else base.capture_user_ids),
        allowed_channels=tuple(allowed_channels if allowed_channels is not None else base.allowed_channels),
        session_idle_minutes=max(1, int(session_idle_minutes or base.session_idle_minutes)),
    )


def load_event_file(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise SlackConnectorError("Slack event file must contain a JSON object")
    return payload


def _safe_auth_test(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "team": payload.get("team"),
        "team_id": payload.get("team_id"),
        "user_id": payload.get("user_id"),
        "bot_id": payload.get("bot_id"),
        "url": payload.get("url"),
    }
