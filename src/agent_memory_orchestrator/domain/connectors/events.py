"""Connector event normalization contracts."""

from __future__ import annotations

from ...integrations.connectors.base import ConnectorEvent
from ...integrations.connectors.slack.events import SlackMessage
from ...integrations.connectors.slack.events import finalize_connector_event
from ...integrations.connectors.slack.events import is_bot_mentioned
from ...integrations.connectors.slack.events import mentioned_user_ids
from ...integrations.connectors.slack.events import message_to_connector_event
from ...integrations.connectors.slack.events import parse_message_envelope
from ...integrations.connectors.slack.events import should_capture_message
from ...integrations.connectors.slack.events import should_reply_message

__all__ = [
    "ConnectorEvent",
    "SlackMessage",
    "finalize_connector_event",
    "is_bot_mentioned",
    "mentioned_user_ids",
    "message_to_connector_event",
    "parse_message_envelope",
    "should_capture_message",
    "should_reply_message",
]
