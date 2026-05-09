from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..base import ConnectorEvent
from .config import SlackConfig


@dataclass(slots=True, frozen=True)
class SlackMessage:
    team_id: str
    channel_id: str
    user_id: str
    text: str
    ts: str
    thread_ts: str
    event_type: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def session_id(self) -> str:
        anchor = self.thread_ts or self.ts
        return f"slack:{self.team_id}:{self.channel_id}:{anchor}"

    @property
    def external_id(self) -> str:
        return f"{self.team_id}:{self.channel_id}:{self.ts}"


def parse_message_envelope(envelope: dict[str, Any]) -> SlackMessage | None:
    payload = envelope.get("payload") if isinstance(envelope.get("payload"), dict) else envelope
    event = payload.get("event") if isinstance(payload.get("event"), dict) else payload
    if not isinstance(event, dict):
        return None

    event_type = str(event.get("type") or "")
    if event_type not in {"message", "app_mention"}:
        return None
    subtype = str(event.get("subtype") or "")
    if subtype and subtype not in {"", "thread_broadcast"}:
        return None
    if event.get("bot_id"):
        return None

    team_id = str(event.get("team") or payload.get("team_id") or envelope.get("team_id") or "")
    channel_id = str(event.get("channel") or "")
    user_id = str(event.get("user") or "")
    text = str(event.get("text") or "")
    ts = str(event.get("ts") or event.get("event_ts") or "")
    thread_ts = str(event.get("thread_ts") or "")
    if not all((team_id, channel_id, user_id, text, ts)):
        return None
    return SlackMessage(
        team_id=team_id,
        channel_id=channel_id,
        user_id=user_id,
        text=text,
        ts=ts,
        thread_ts=thread_ts,
        event_type=event_type,
        raw=event,
    )


def should_capture_message(message: SlackMessage, config: SlackConfig) -> tuple[bool, str]:
    if config.allowed_channels and message.channel_id not in config.allowed_channels:
        return False, "channel_not_allowed"
    if is_bot_mentioned(message.text, config.bot_user_id):
        return True, "bot_mentioned"
    if message.user_id in set(config.capture_user_ids):
        return True, "captured_user_message"
    mentioned_users = set(mentioned_user_ids(message.text))
    if mentioned_users.intersection(config.capture_user_ids):
        return True, "captured_user_mentioned"
    return False, "not_relevant"


def should_reply_message(message: SlackMessage, config: SlackConfig) -> bool:
    if config.reply_only_when_mentioned:
        return is_bot_mentioned(message.text, config.bot_user_id)
    return message.channel_id.startswith("D") or is_bot_mentioned(message.text, config.bot_user_id)


def message_to_connector_event(message: SlackMessage, *, capture_reason: str, reply_required: bool) -> ConnectorEvent:
    return ConnectorEvent(
        connector="slack",
        source_app="slack",
        external_id=message.external_id,
        session_id=message.session_id,
        event_type="slack_message",
        content=message.text,
        metadata={
            "team_id": message.team_id,
            "channel_id": message.channel_id,
            "user_id": message.user_id,
            "ts": message.ts,
            "thread_ts": message.thread_ts,
            "slack_event_type": message.event_type,
            "capture_reason": capture_reason,
            "reply_required": reply_required,
        },
    )


def finalize_connector_event(*, session_id: str, reason: str, message_count: int = 0) -> ConnectorEvent:
    return ConnectorEvent(
        connector="slack",
        source_app="slack",
        external_id=f"{session_id}:finalize",
        session_id=session_id,
        event_type="connector_session_finalize",
        content=f"Finalize Slack connector session: {reason}",
        metadata={"reason": reason, "message_count": message_count},
    )


def is_bot_mentioned(text: str, bot_user_id: str) -> bool:
    if not bot_user_id:
        return False
    return f"<@{bot_user_id}>" in str(text or "")


def mentioned_user_ids(text: str) -> list[str]:
    return re.findall(r"<@([A-Z0-9]+)>", str(text or ""))
